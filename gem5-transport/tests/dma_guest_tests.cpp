// Exercise the real userspace controller against a deterministic driver boundary.
// Actual IRQ/coherence/kernel validation remains in the full-system tests.
#include <chimaera/dma_controller.hpp>
#include <gem5/chimaera_dma.h>
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <mutex>
#include <stdexcept>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>

using chimaera::Message;
using namespace std::chrono_literals;
namespace {
void expect(bool value) { if (!value) throw std::runtime_error("DMA ownership/progress regression"); }
struct Driver {
    std::mutex mutex;
    int fd{-1};
    chdma_info info{CHDMA_ABI_VERSION, 2, 3, 8 * 1024 * 1024, CHDMA_PAGE_BYTES, 0};
    std::vector<std::byte*> buffers;
    std::deque<unsigned> posted;
    std::deque<Message> packets;
    std::deque<chdma_completion> completions;
    unsigned closed{}, rx_posts{}, tx_submits{};
    void completion(chdma_completion value) {
        if (completions.empty()) {
            std::uint64_t one = 1;
            expect(::write(fd, &one, sizeof(one)) == sizeof(one));
        }
        completions.push_back(value);
    }
    void deliver() {
        while (!packets.empty() && !posted.empty()) {
            auto bytes = std::move(packets.front()); packets.pop_front();
            auto slot = posted.front(); posted.pop_front();
            expect(bytes.size() <= info.slot_bytes);
            if (bytes.size()) std::memcpy(buffers[slot], bytes.data(), bytes.size());
            completion({CHDMA_IRQ_RX, slot, static_cast<unsigned>(bytes.size()), 0});
        }
    }
    void inject(Message value) {
        std::lock_guard lock(mutex);
        packets.push_back(std::move(value));
        deliver();
    }
} driver;
Message pattern(std::size_t size, unsigned seed) {
    auto bytes = Message::for_overwrite(size);
    for (std::size_t i = 0; i < size; ++i) bytes[i] = static_cast<std::byte>((i + seed) % 251);
    return bytes;
}
template<class Predicate> void until(chimaera::DmaGuestController& controller, Predicate predicate) {
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    do {
        const auto result = controller.run_next();
        if (!result.ok()) throw std::runtime_error(result.message);
        if (predicate()) return;
        std::this_thread::sleep_for(100us);
    } while (std::chrono::steady_clock::now() < deadline);
    throw std::runtime_error("DMA controller failed to make progress");
}
}
extern "C" {
int __real_open(const char*, int, ...);
int __real_close(int);
ssize_t __real_read(int, void*, size_t);
int __real_ioctl(int, unsigned long, ...);
void* __real_mmap(void*, size_t, int, int, int, off_t);
int __wrap_open(const char* path, int flags, ...) {
    if (std::strcmp(path, "test-dma") == 0) {
        std::lock_guard lock(driver.mutex);
        expect(driver.fd == -1);
        driver.fd = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        driver.buffers.resize(driver.info.rx_slots + driver.info.tx_slots);
        return driver.fd;
    }
    if (flags & O_CREAT) {
        va_list args; va_start(args, flags); const auto mode = va_arg(args, int); va_end(args);
        return __real_open(path, flags, mode);
    }
    return __real_open(path, flags);
}
int __wrap_close(int fd) {
    if (fd == driver.fd) {
        std::lock_guard lock(driver.mutex);
        driver.fd = -1; ++driver.closed;
        driver.posted.clear(); driver.packets.clear(); driver.completions.clear();
    }
    return __real_close(fd);
}
void* __wrap_mmap(void* address, size_t size, int protection, int flags, int fd, off_t offset) {
    if (fd != driver.fd) return __real_mmap(address, size, protection, flags, fd, offset);
    std::lock_guard lock(driver.mutex);
    const auto slot = offset / CHDMA_PAGE_BYTES;
    expect(slot >= 0 && static_cast<size_t>(slot) < driver.buffers.size());
    auto* bytes = __real_mmap(nullptr, size, protection, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
    driver.buffers[slot] = static_cast<std::byte*>(bytes);
    return bytes;
}
int __wrap_ioctl(int fd, unsigned long command, ...) {
    va_list args; va_start(args, command); auto* value = va_arg(args, void*); va_end(args);
    if (fd != driver.fd) return __real_ioctl(fd, command, value);
    std::lock_guard lock(driver.mutex);
    if (command == CHDMA_IOC_INFO) { *static_cast<chdma_info*>(value) = driver.info; return 0; }
    const auto request = *static_cast<chdma_submit*>(value);
    if (command == CHDMA_IOC_RX) {
        ++driver.rx_posts;
        driver.posted.push_back(request.slot);
        driver.deliver();
        return 0;
    }
    expect(command == CHDMA_IOC_TX && request.slot >= driver.info.rx_slots);
    ++driver.tx_submits;
    driver.packets.emplace_back(driver.buffers[request.slot], driver.buffers[request.slot] + request.length);
    driver.completion({CHDMA_IRQ_TX, request.slot, 0, 0});
    driver.deliver();
    return 0;
}
ssize_t __wrap_read(int fd, void* output, size_t size) {
    if (fd != driver.fd) return __real_read(fd, output, size);
    std::lock_guard lock(driver.mutex);
    if (driver.completions.empty()) { errno = EAGAIN; return -1; }
    expect(size >= sizeof(chdma_completion));
    *static_cast<chdma_completion*>(output) = driver.completions.front();
    driver.completions.pop_front();
    if (driver.completions.empty()) {
        std::uint64_t count;
        expect(__real_read(fd, &count, sizeof(count)) == sizeof(count));
    }
    return sizeof(chdma_completion);
}
}
int main() {
    std::atomic<unsigned> notifications{};
    auto controller = std::make_unique<chimaera::DmaGuestController>([&] { ++notifications; }, "test-dma");
    constexpr char startup[] = "CHDMA002";
    driver.inject(Message(reinterpret_cast<const std::byte*>(startup),
                          reinterpret_cast<const std::byte*>(startup) + 8));
    until(*controller, [&] { return controller->initialized(); });
    expect(!controller->take());
    bool oversized = false;
    try { controller->allocate(driver.info.slot_bytes - 15); }
    catch (const std::length_error&) { oversized = true; }
    expect(oversized); // Prefix headroom is included in the registered capacity.
    const std::size_t size = 1843337;
    auto first = controller->allocate(size);
    auto second = controller->allocate(size);
    auto fallback = controller->allocate(size);
    auto fill = [&](Message& value, unsigned seed) {
        auto expected = pattern(size, seed);
        std::memcpy(value.data(), expected.data(), size);
    };
    fill(first, 1); fill(second, 2); fill(fallback, 3);
    // The oldest frame needs staging while all direct allocation slots are
    // occupied by younger queued frames. The reserved slot guarantees progress.
    controller->submit(std::move(fallback));
    controller->submit(std::move(first));
    controller->submit(std::move(second));
    std::optional<Message> held, other;
    until(*controller, [&] { if (!held) held = controller->take(); return held.has_value(); });
    until(*controller, [&] { if (!other) other = controller->take(); return other.has_value(); });
    expect(*held == pattern(size, 3) && *other == pattern(size, 1));
    for (unsigned i = 0; i < 20; ++i) {
        expect(controller->run_next().ok());
        std::this_thread::sleep_for(100us);
    }
    expect(!controller->take()); // Both RX buffers remain loaned, so RX is full.
    expect(*held == pattern(size, 3) && *other == pattern(size, 1));
    held.reset();
    until(*controller, [&] { held = controller->take(); return held.has_value(); });
    expect(*held == pattern(size, 2));
    other.reset();
    auto replacement = controller->allocate(64);
    std::fill(replacement.begin(), replacement.end(), std::byte{8});
    controller->submit(std::move(replacement), "latest");
    auto newest = controller->allocate(64);
    std::fill(newest.begin(), newest.end(), std::byte{9});
    controller->submit(std::move(newest), "latest");
    until(*controller, [&] { other = controller->take(); return other.has_value(); });
    expect(*other == Message(64, std::byte{9}));
    expect(driver.tx_submits == 4 && notifications > 0);
    expect(controller->statistics().find("staging_bytes=1843337") != std::string::npos);
    const auto posts = driver.rx_posts;
    controller.reset(); // External loans retain mappings and suppress reposts.
    expect(driver.closed == 0 && *held == pattern(size, 2));
    other.reset(); held.reset();
    expect(driver.closed == 1 && driver.rx_posts == posts);
    auto bad = std::make_unique<chimaera::DmaGuestController>([] {}, "test-dma");
    driver.inject(Message(8, std::byte{42}));
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while (!bad->has_work() && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    expect(!bad->run_next().ok());
    expect(!bad->run_next().ok()); // A malformed packet cannot restart the session.
    bool rejected = false;
    try { bad->submit(Message(1)); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected);
    bad.reset();
    expect(driver.closed == 2);
}
