#include <chimaera/dma_controller.hpp>
#include <chimaera/outgoing_queue.hpp>
#include <gem5/chimaera_dma.h>
#include "controller_protocol.hpp"
#include "dma_protocol.hpp"

#include <cerrno>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <mutex>
#include <poll.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

namespace chimaera {
namespace {
[[noreturn]] void system_error(const char* operation) {
    throw std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
}

// Loans retain the mapping/file after controller teardown. Closing a live file
// drains DMA in the driver, so no page is unmapped or recycled under a request.
struct Pool : std::enable_shared_from_this<Pool> {
    int fd{-1};
    chdma_info info{};
    std::vector<std::byte*> buffers;
    std::vector<bool> allocated;
    std::mutex mutex;
    bool alive{true};
    std::string failure;
    std::function<void()> notify;
    std::uint64_t direct{}, fallback{}, staging_bytes{};

    ~Pool() {
        for (auto* address : buffers) if (address) ::munmap(address, info.slot_bytes);
        if (fd >= 0) ::close(fd);
    }
    void post(unsigned slot) {
        chdma_submit request{slot, info.slot_bytes};
        if (::ioctl(fd, CHDMA_IOC_RX, &request) < 0) system_error("post DMA receive buffer");
    }
    void release(unsigned slot) noexcept {
        bool wake = false;
        {
            std::lock_guard lock(mutex);
            allocated[slot] = false;
            if (alive && slot < info.rx_slots) {
                try { post(slot); }
                catch (const std::exception& e) { failure = e.what(); wake = true; }
            }
        }
        if (wake) { try { notify(); } catch (...) {} }
    }
    struct Loan {
        std::shared_ptr<Pool> pool;
        unsigned slot;
        Loan(std::shared_ptr<Pool> p, unsigned s) : pool(std::move(p)), slot(s) {}
        ~Loan() { pool->release(slot); }
    };
    Message loan(unsigned slot, std::size_t size) {
        // Construct the lease before marking ownership; if allocation throws,
        // the caller still has the completion/slot and can fail safely.
        auto owner = std::make_shared<Loan>(shared_from_this(), slot);
        allocated[slot] = true;
        return Message::adopt(buffers[slot] + (slot < info.rx_slots ? 0 : dma::header_bytes),
                              size, std::move(owner));
    }
    Message allocate(std::size_t size, bool stage = false) {
        if (size > info.slot_bytes - dma::header_bytes)
            throw std::length_error("DMA frame exceeds registered slot size including framing");
        std::lock_guard lock(mutex);
        // Reserve the last TX buffer for heap fallback. Otherwise a heap frame
        // preceding queued loans could deadlock waiting for those loans' slots.
        const auto first = stage ? buffers.size() - 1 : info.rx_slots;
        const auto last = stage ? buffers.size() : buffers.size() - 1;
        for (auto slot = first; slot < last; ++slot) {
            if (!allocated[slot]) {
                if (!stage) ++direct;
                return loan(slot, size);
            }
        }
        if (stage) throw std::logic_error("DMA staging slot still owned without an active TX");
        ++fallback;
        return Message::for_overwrite(size);
    }
    unsigned transmit_slot(const Message& data) const {
        for (unsigned slot = info.rx_slots; slot < buffers.size(); ++slot)
            if (buffers[slot] + dma::header_bytes == data.data()) return slot;
        return buffers.size();
    }
};
} // namespace

struct DmaGuestController::Impl {
    std::shared_ptr<Pool> pool{std::make_shared<Pool>()};
    control::OutgoingQueue outgoing;
    control::ReceivedQueue incoming;
    std::optional<Message> active;
    std::mutex events_mutex;
    std::deque<chdma_completion> events;
    std::string failure;
    int stop_fd{-1};
    std::thread worker;
    bool started{};
    std::string application_failure;
    std::uint64_t rx_completed{}, tx_completed{}, tx_submitted{};

    Impl(std::function<void()> notify, const std::string& device,
         const std::string& module, const std::string& options) {
        pool->notify = std::move(notify);
        if (!pool->notify) throw std::invalid_argument("DMA requires a completion notifier");
        if (!module.empty()) {
            const int module_fd = ::open(module.c_str(), O_RDONLY | O_CLOEXEC);
            if (module_fd < 0) system_error("open DMA module");
            const auto result = ::syscall(SYS_finit_module, module_fd, options.c_str(), 0);
            const auto saved = errno;
            ::close(module_fd);
            errno = saved;
            if (result < 0 && errno != EEXIST) system_error("load DMA module");
        }
        pool->fd = ::open(device.c_str(), O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (pool->fd < 0) system_error("open DMA device");
        if (::ioctl(pool->fd, CHDMA_IOC_INFO, &pool->info) < 0) system_error("query DMA pool");
        const auto& info = pool->info;
        if (info.abi_version != CHDMA_ABI_VERSION || info.page_bytes != CHDMA_PAGE_BYTES ||
            !info.rx_slots || info.tx_slots < 2 || info.rx_slots + info.tx_slots > 64 ||
            !info.slot_bytes || info.slot_bytes > CHDMA_MAX_BYTES || info.slot_bytes % info.page_bytes)
            throw std::runtime_error("invalid DMA driver ABI/pool geometry (at least two TX slots required)");
        pool->allocated.resize(info.rx_slots + info.tx_slots);
        pool->buffers.resize(pool->allocated.size());
        for (unsigned slot = 0; slot < pool->buffers.size(); ++slot) {
            auto* memory = ::mmap(nullptr, info.slot_bytes, PROT_READ | PROT_WRITE,
                MAP_SHARED, pool->fd, static_cast<off_t>(slot) * info.page_bytes);
            if (memory == MAP_FAILED) system_error("map DMA pool slot");
            pool->buffers[slot] = static_cast<std::byte*>(memory);
        }
        for (unsigned slot = 0; slot < info.rx_slots; ++slot) pool->post(slot);
        stop_fd = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (stop_fd < 0) system_error("create DMA cancellation event");
        try { worker = std::thread([this] { receive(); }); }
        catch (...) { ::close(stop_fd); throw; }
    }
    ~Impl() {
        {
            std::lock_guard lock(pool->mutex);
            pool->alive = false;
        }
        std::uint64_t one = 1;
        if (stop_fd >= 0) {
            ssize_t count;
            do { count = ::write(stop_fd, &one, sizeof(one)); } while (count < 0 && errno == EINTR);
        }
        if (worker.joinable()) worker.join();
        if (stop_fd >= 0) ::close(stop_fd);
        // Active and queued loans release after worker shutdown. External RX
        // loans may outlive this controller and keep Pool alive until released.
    }
    void receive() noexcept {
        try {
            pollfd descriptors[2]{{pool->fd, POLLIN, 0}, {stop_fd, POLLIN, 0}};
            while (true) {
                const auto result = ::poll(descriptors, 2, -1);
                if (result < 0 && errno == EINTR) continue;
                if (result < 0) system_error("wait for DMA completion");
                if (descriptors[1].revents) return;
                if (descriptors[0].revents & (POLLERR | POLLHUP | POLLNVAL))
                    throw std::runtime_error("DMA driver fault/disconnection");
                while (true) {
                    chdma_completion completion{};
                    const auto count = ::read(pool->fd, &completion, sizeof(completion));
                    if (count < 0 && errno == EINTR) continue;
                    if (count < 0 && errno == EAGAIN) break;
                    if (count != sizeof(completion)) system_error("read DMA completion");
                    {
                        std::lock_guard lock(events_mutex);
                        if (events.size() >= 64) throw std::runtime_error("DMA completion queue overflow");
                        events.push_back(completion);
                    }
                }
                pool->notify();
            }
        } catch (const std::exception& e) {
            {
                std::lock_guard lock(events_mutex);
                failure = e.what();
            }
            try { pool->notify(); } catch (...) {}
        }
    }
    void service() {
        std::deque<chdma_completion> completed;
        {
            std::lock_guard lock(events_mutex);
            if (!failure.empty()) throw std::runtime_error(failure);
            completed.swap(events);
        }
        {
            std::lock_guard lock(pool->mutex);
            if (!pool->failure.empty()) throw std::runtime_error(pool->failure);
        }
        for (const auto& completion : completed) {
            if (completion.events == CHDMA_IRQ_TX) {
                ++tx_completed;
                if (!active || pool->transmit_slot(*active) != completion.slot)
                    throw std::runtime_error("unexpected DMA transmit completion");
                active.reset();
            } else if (completion.events == CHDMA_IRQ_RX) {
                ++rx_completed;
                if (completion.slot >= pool->info.rx_slots || completion.length > pool->info.slot_bytes)
                    throw std::runtime_error("invalid DMA receive completion");
                Message bytes;
                {
                    std::lock_guard lock(pool->mutex);
                    if (pool->allocated[completion.slot]) throw std::runtime_error("DMA receive slot reused while loaned");
                    bytes = pool->loan(completion.slot, completion.length);
                }
                if (!started) {
                    if (bytes.size() != sizeof(dma::startup) - 1 ||
                        std::memcmp(bytes.data(), dma::startup, sizeof(dma::startup) - 1))
                        throw std::runtime_error("DMA transport startup version mismatch");
                    started = true;
                } else {
                    incoming.append(dma::decode(std::move(bytes)));
                }
            } else {
                throw std::runtime_error("DMA device error " + std::to_string(completion.error));
            }
        }
        if (!started || active || !outgoing.size()) return;
        const auto layout = dma::layout(outgoing.entries());
        Message bytes;
        if (layout.count > 1) {
            bytes = pool->allocate(layout.size() - dma::header_bytes, true);
            dma::encode(bytes.data() - dma::header_bytes, layout, outgoing.entries());
            pool->staging_bytes += layout.payload;
            for (std::size_t i = 0; i < layout.count; ++i) (void)outgoing.take();
        } else {
            auto entry = outgoing.take();
            bytes = std::move(entry->data);
            if (pool->transmit_slot(bytes) == pool->buffers.size()) {
                auto registered = pool->allocate(bytes.size(), true);
                if (bytes.size()) std::memcpy(registered.data(), bytes.data(), bytes.size());
                pool->staging_bytes += bytes.size();
                bytes = std::move(registered);
            }
            // The allocator reserved the prefix before the application frame.
            // Adding its header never copies an image's serialized bytes.
            dma::header(bytes.data() - dma::header_bytes, layout);
        }
        const auto slot = pool->transmit_slot(bytes);
        chdma_submit request{slot, static_cast<std::uint32_t>(layout.size())};
        if (::ioctl(pool->fd, CHDMA_IOC_TX, &request) < 0) system_error("submit DMA transmit");
        active = std::move(bytes);
        ++tx_submitted;
    }
};

DmaGuestController::DmaGuestController(std::function<void()> notify, std::string device,
        std::string module, std::string options)
    : impl_(std::make_unique<Impl>(std::move(notify), device, module, options)) {}
DmaGuestController::~DmaGuestController() = default;
Message DmaGuestController::allocate(std::size_t size) {
    if (!impl_->application_failure.empty()) throw std::runtime_error(impl_->application_failure);
    return impl_->pool->allocate(size);
}
void DmaGuestController::submit(Message data, std::string_view key) {
    if (!impl_->application_failure.empty()) throw std::runtime_error(impl_->application_failure);
    if (data.size() > impl_->pool->info.slot_bytes - dma::header_bytes)
        throw std::length_error("DMA frame exceeds registered slot size");
    impl_->outgoing.submit(std::move(data), key);
}
std::optional<Message> DmaGuestController::take() { return impl_->incoming.take(); }
ControllerResult DmaGuestController::run_next() {
    if (!impl_->application_failure.empty())
        return {ControllerState::failed, impl_->application_failure};
    try { impl_->service(); return {}; }
    catch (const std::exception& e) {
        impl_->application_failure = e.what();
        return {ControllerState::failed, impl_->application_failure};
    }
}
bool DmaGuestController::initialized() const noexcept { return impl_->started; }
bool DmaGuestController::has_work() const {
    if (!impl_->application_failure.empty()) return true;
    if (!impl_->incoming.empty()) return true;
    std::lock_guard lock(impl_->events_mutex);
    return !impl_->events.empty() || !impl_->failure.empty();
}
std::string DmaGuestController::statistics() const {
    const auto& p = *impl_->pool;
    return "direct_allocations=" + std::to_string(p.direct) + " heap_fallbacks=" +
        std::to_string(p.fallback) + " staging_bytes=" + std::to_string(p.staging_bytes) +
        " rx_completed=" + std::to_string(impl_->rx_completed) +
        " tx_completed=" + std::to_string(impl_->tx_completed) +
        " tx_submitted=" + std::to_string(impl_->tx_submitted) +
        " outgoing=" + std::to_string(impl_->outgoing.size()) +
        " active=" + std::to_string(impl_->active.has_value());
}
} // namespace chimaera
