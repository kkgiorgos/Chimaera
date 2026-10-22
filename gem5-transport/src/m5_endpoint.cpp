#include <chimaera/m5_endpoint.hpp>
#include <gem5/chimaera_m5.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <atomic>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <mutex>
#include <stdexcept>
#include <system_error>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

namespace chimaera {
static_assert(m5_application_port_bit == CHM5_APPLICATION_PORT_BIT);
static_assert(m5_bulk_port_bit == CHM5_BULK_PORT_BIT);
namespace {
[[noreturn]] void fail(int error, const char* what) {
    throw std::system_error(error, std::generic_category(), what);
}
}
struct M5Endpoint::Impl {
    int fd{-1};
    GuestM5Ops ops;
    chm5_bind binding{};
    std::vector<std::byte*> buffers;
    std::mutex mutex;
    std::atomic<int> failure{};
    bool bound{};

    explicit Impl(GuestM5Ops mode) : ops(mode) {}
    ~Impl() {
        if (bound) {
            auto request = base();
            call(false, CHM5_CTL_CLOSE, request);
        }
        for (auto* buffer : buffers) ::munmap(buffer, binding.slot_bytes);
        if (fd >= 0) ::close(fd); // synchronous physical unregister before pages free
    }
    chm5_request base() const {
        chm5_request request{};
        request.version = CHM5_ABI_VERSION;
        request.size = sizeof(request);
        request.context = binding.context;
        request.lane = binding.lane;
        request.generation = binding.generation;
        request.process = binding.process;
        return request;
    }
    int call(bool data, uint64_t command, chm5_request& request) const {
        // External function calls are compiler barriers; these fences also
        // document the descriptor/payload publication contract. x86 wrappers
        // mfence around the trigger; detailed opcodes serialize both sides.
        std::atomic_thread_fence(std::memory_order_seq_cst);
        const auto operation = data ?
            (ops == GuestM5Ops::address ? m5_chimaera_data_addr : m5_chimaera_data) :
            (ops == GuestM5Ops::address ? m5_chimaera_control_addr : m5_chimaera_control);
        const auto result = static_cast<int64_t>(operation(command, &request, sizeof(request)));
        std::atomic_thread_fence(std::memory_order_seq_cst);
        return result < 0 && result >= -4095 ? static_cast<int>(-result) :
            result == 0 ? 0 : EPROTO;
    }
    void check() const {
        if (const auto error = failure.load(std::memory_order_acquire))
            fail(error, "Chimaera m5 endpoint");
    }
    void release(uint32_t slot, uint64_t sequence) noexcept {
        std::lock_guard lock(mutex);
        auto request = base();
        request.slot = slot;
        request.packet_sequence = sequence;
        const auto error = call(true, CHM5_DATA_RELEASE, request);
        if (error) failure.store(error, std::memory_order_release);
    }
    void release_batch(std::span<const chm5_completion> completions) noexcept {
        if (completions.empty()) return;
        std::lock_guard lock(mutex);
        auto request = base();
        request.address0 = reinterpret_cast<uintptr_t>(completions.data());
        request.bytes0 = completions.size_bytes();
        const auto error = call(true, CHM5_DATA_RELEASE_BATCH, request);
        if (error) failure.store(error, std::memory_order_release);
    }
};

M5Endpoint::M5Endpoint(M5Lane lane, std::string device, GuestM5Ops ops)
    : impl_(std::make_shared<Impl>(ops)) {
    auto& state = *impl_;
    if (lane != M5Lane::control && lane != M5Lane::bulk)
        throw std::invalid_argument("invalid Chimaera m5 lane");
    if (ops == GuestM5Ops::address && (!m5_mem || m5_mem == MAP_FAILED))
        fail(ENXIO, "map libm5 address operations before creating M5Endpoint");
    state.fd = ::open(device.c_str(), O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (state.fd < 0) fail(errno, "open Chimaera m5 notification device");
    state.binding.version = CHM5_ABI_VERSION;
    state.binding.lane = static_cast<uint32_t>(lane);
    if (::ioctl(state.fd, CHM5_IOC_BIND, &state.binding) < 0)
        fail(errno, "bind Chimaera m5 lane");
    if (state.binding.version != CHM5_ABI_VERSION ||
        state.binding.process != static_cast<uint32_t>(::getpid()) ||
        state.binding.lane != static_cast<uint32_t>(lane) ||
        !state.binding.generation || !state.binding.slots || state.binding.slots > CHM5_MAX_SLOTS ||
        !state.binding.slot_bytes || state.binding.slot_bytes > CHM5_MAX_BYTES ||
        state.binding.slot_bytes % CHM5_PAGE_BYTES)
        fail(EPROTO, "invalid Chimaera m5 kernel binding");
    state.bound = true;
    for (uint32_t slot = 0; slot < state.binding.slots; ++slot) {
        void* mapping = ::mmap(nullptr, state.binding.slot_bytes, PROT_READ | PROT_WRITE,
            MAP_SHARED, state.fd, static_cast<off_t>(slot) * CHM5_PAGE_BYTES);
        if (mapping == MAP_FAILED) fail(errno, "map Chimaera m5 RX slot");
        state.buffers.push_back(static_cast<std::byte*>(mapping));
        // Install guest page table mappings outside the application's timing ROI.
        for (size_t offset = 0; offset < state.binding.slot_bytes; offset += CHM5_PAGE_BYTES)
            (void)*reinterpret_cast<volatile const std::byte*>(state.buffers.back() + offset);
    }
    auto request = state.base();
    if (const auto error = state.call(false, CHM5_CTL_BIND, request)) fail(error, "confirm Chimaera m5 ABI");
    if (request.length != state.binding.slot_bytes || request.slot != state.binding.slots)
        fail(EPROTO, "Chimaera m5 kernel/model pool disagreement");
}
M5Endpoint::~M5Endpoint() = default;

bool M5Endpoint::send(std::span<const std::byte> header, std::span<const std::byte> body) {
    auto& state = *impl_;
    std::lock_guard lock(state.mutex);
    state.check();
    if (header.size() != 52 || body.size() > CHM5_MAX_BYTES - header.size())
        fail(EMSGSIZE, "invalid Chimaera m5 CDDS frame size");
    auto request = state.base();
    request.address0 = reinterpret_cast<uintptr_t>(header.data());
    request.bytes0 = header.size();
    request.address1 = reinterpret_cast<uintptr_t>(body.data());
    request.bytes1 = body.size();
    const auto error = state.call(true, CHM5_DATA_SUBMITV, request);
    if (error == EAGAIN || error == ENOBUFS) return false;
    if (error) fail(error, "submit Chimaera m5 frame");
    return true;
}
void M5Endpoint::open_channel(uint32_t port) {
    auto& state = *impl_;
    std::lock_guard lock(state.mutex);
    state.check();
    auto request = state.base();
    request.port = port;
    if (const auto error = state.call(false, CHM5_CTL_OPEN_CHANNEL, request))
        fail(error, "open Chimaera m5 channel");
}
void M5Endpoint::close_channel(uint32_t port) {
    auto& state = *impl_;
    std::lock_guard lock(state.mutex);
    state.check();
    auto request = state.base();
    request.port = port;
    if (const auto error = state.call(false, CHM5_CTL_CLOSE_CHANNEL, request))
        fail(error, "close Chimaera m5 channel");
}
std::optional<Message> M5Endpoint::take() {
    auto state = impl_;
    std::unique_lock lock(state->mutex);
    state->check();
    auto request = state->base();
    const auto error = state->call(true, CHM5_DATA_REAP, request);
    if (error == EAGAIN) {
        // ARM observes the persistent level after the empty reap. An arrival
        // before/after this call remains visible to the driver's poll callback.
        auto arm = state->base();
        if (const auto result = state->call(false, CHM5_CTL_ARM, arm))
            fail(result, "arm Chimaera m5 notification");
        if (arm.status & (CHM5_STATUS_ERROR | CHM5_STATUS_SHUTDOWN))
            fail(arm.error ? arm.error : ESHUTDOWN, "Chimaera m5 endpoint stopped");
        return std::nullopt;
    }
    if (error) fail(error, "reap Chimaera m5 frame");
    if (request.slot >= state->buffers.size() || request.length > state->binding.slot_bytes)
        fail(EPROTO, "invalid Chimaera m5 completion");
    auto* storage = state->buffers[request.slot];
    lock.unlock();
    // The owner retains the entire endpoint (and its mappings/fd). Loan release
    // reposts this physical buffer only after OnDataReceived and all slices end.
    std::shared_ptr<void> loan(storage, [state, slot = request.slot,
                                      sequence = request.packet_sequence](void*) {
        state->release(slot, sequence);
    });
    return Message::adopt(storage, request.length, std::move(loan));
}
int M5Endpoint::completion_fd() const { return impl_->fd; }

std::vector<Message> M5Endpoint::take_batch() {
    struct Loan {
        std::shared_ptr<Impl> state;
        std::array<chm5_completion, CHM5_MAX_SLOTS> entries{};
        std::size_t count{};
        ~Loan() { state->release_batch({entries.data(), count}); }
    };
    auto state = impl_;
    std::vector<Message> messages;
    messages.reserve(state->buffers.size());
    auto loan = std::make_shared<Loan>();
    loan->state = state;
    std::unique_lock lock(state->mutex);
    state->check();
    auto request = state->base();
    request.address0 = reinterpret_cast<uintptr_t>(loan->entries.data());
    request.bytes0 = state->buffers.size() * sizeof(chm5_completion);
    const auto error = state->call(true, CHM5_DATA_REAP_BATCH, request);
    // Driver poll installs the waiter before checking persistent RX level.
    // Empty reaps therefore need no extra ARM pseudo-op in this batch path.
    if (error == EAGAIN) return messages;
    if (error) fail(error, "reap Chimaera m5 batch");
    if (!request.length || request.length > state->buffers.size())
        fail(EPROTO, "invalid Chimaera m5 completion count");
    std::array<bool, CHM5_MAX_SLOTS> used{};
    for (std::size_t index = 0; index < request.length; ++index) {
        const auto& entry = loan->entries[index];
        if (entry.slot >= state->buffers.size() || used[entry.slot] ||
            entry.length > state->binding.slot_bytes)
            fail(EPROTO, "invalid Chimaera m5 batch completion");
        used[entry.slot] = true;
    }
    loan->count = request.length;
    lock.unlock();
    for (std::size_t index = 0; index < loan->count; ++index) {
        const auto& entry = loan->entries[index];
        messages.push_back(Message::adopt(state->buffers[entry.slot], entry.length, loan));
    }
    return messages;
}
} // namespace chimaera
