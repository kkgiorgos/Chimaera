#include <chimaera/m5_host_controller.hpp>
#include <chimaera/outgoing_queue.hpp>
#include <gem5/chimaera_mailbox.hh>
#include <gem5/chimaera_m5.h>

#include <array>
#include <cstring>
#include <sstream>

namespace chimaera {
namespace {
// The model can retain 64 MiB beyond sixteen 32 MiB mailbox slots per lane.
// Keep enough bounded host storage for one complete accepted boundary, including
// framing. Storage is allocated only for actual messages, not at construction.
constexpr std::size_t ingress_bytes = 128 * 1024 * 1024;
constexpr std::size_t egress_bytes = 640 * 1024 * 1024;
unsigned lane(std::span<const std::byte> frame) {
    if (frame.size() < 72 || std::memcmp(frame.data(), "CDDS", 4) ||
        frame[4] != std::byte{1} || frame[5] != std::byte{3} ||
        frame[6] != std::byte{} || frame[7] != std::byte{} ||
        std::memcmp(frame.data() + 52, "RTPS", 4))
        throw std::runtime_error("m5ops host requires a complete CDDS data packet");
    auto word = [&](std::size_t offset) {
        std::uint32_t value = 0;
        for (unsigned i = 0; i < 4; ++i)
            value |= std::to_integer<std::uint32_t>(frame[offset + i]) << (8 * i);
        return value;
    };
    if (word(48) != frame.size() - 52)
        throw std::runtime_error("m5ops host received inconsistent CDDS length");
    return (word(28) & CHM5_BULK_PORT_BIT) ? CHM5_BULK : CHM5_CONTROL;
}
} // namespace

struct M5HostController::Impl {
    Gem5TimingController& timing;
    std::array<std::unique_ptr<chimaera_mailbox::Mailbox>, 2> h2g, g2h;
    std::array<control::OutgoingQueue, 2> outgoing;
    std::array<control::OutgoingQueue, 2> incoming;
    const std::size_t pendingLimit;
    bool stopped{};
    std::string failure;
    Impl(Gem5TimingController& t, const std::string& input, const std::string& output,
         std::size_t pending)
        : timing(t),
          outgoing{control::OutgoingQueue(pending, ingress_bytes),
                   control::OutgoingQueue(pending, ingress_bytes)},
          incoming{control::OutgoingQueue(pending, egress_bytes),
                   control::OutgoingQueue(pending, egress_bytes)},
          pendingLimit(pending) {
        for (unsigned i = 0; i < 2; ++i) {
            const std::string suffix = i ? ".bulk" : ".control";
            h2g[i] = std::make_unique<chimaera_mailbox::Mailbox>(input + suffix, true, 16);
            g2h[i] = std::make_unique<chimaera_mailbox::Mailbox>(output + suffix, true, 16);
        }
    }
};

M5HostController::M5HostController(Gem5TimingController& timing, std::string h2g,
        std::string g2h, std::size_t pending)
    : impl_(std::make_unique<Impl>(timing, h2g, g2h, pending)) {}
M5HostController::~M5HostController() = default;
void M5HostController::submit(Message data, std::string_view key) {
    auto& s = *impl_;
    if (s.stopped) throw std::runtime_error("m5ops host stopped");
    if (!s.failure.empty()) throw std::runtime_error(s.failure);
    if (data.size() > CHM5_MAX_BYTES) throw std::length_error("m5ops mailbox frame exceeds 32 MiB");
    const auto selected = lane(data);
    s.outgoing[selected].submit(std::move(data), key);
}
bool M5HostController::try_submit(Message data, std::string_view key) {
    auto& s = *impl_;
    if (s.stopped) throw std::runtime_error("m5ops host stopped");
    if (!s.failure.empty()) throw std::runtime_error(s.failure);
    if (data.size() > CHM5_MAX_BYTES) throw std::length_error("m5ops mailbox frame exceeds 32 MiB");
    auto& queue = s.outgoing[lane(data)];
    auto records = queue.size();
    auto bytes = queue.bytes();
    if (!key.empty()) {
        for (const auto& entry : queue.entries()) {
            if (entry.key == key) {
                --records;
                bytes -= entry.data.size();
            }
        }
    }
    if (records >= s.pendingLimit || data.size() > ingress_bytes - bytes)
        return false;
    queue.submit(std::move(data), key);
    return true;
}
std::optional<Message> M5HostController::take() {
    for (auto& queue : impl_->incoming)
        if (auto entry = queue.take()) return std::move(entry->data);
    return std::nullopt;
}
std::size_t M5HostController::transfer_received(
        const std::function<bool(Message)>& receiver) {
    auto& s = *impl_;
    if (!s.failure.empty()) throw std::runtime_error(s.failure);
    std::size_t count = 0;
    for (auto& queue : s.incoming) {
        while (auto* entry = queue.front()) {
            if (!receiver(entry->data.slice(0, entry->data.size()))) break;
            (void)queue.take();
            ++count;
        }
    }
    return count;
}
ControllerResult M5HostController::step(Duration interval) {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    if (!s.failure.empty()) return {ControllerState::failed, s.failure};
    if (interval.count() <= 0 || interval > std::chrono::hours(1))
        return {ControllerState::failed, "m5ops interval must be positive and at most one hour"};
    try {
        // An occupied bulk ring cannot consume control admission credits.
        for (unsigned i = 0; i < 2; ++i) {
            while (const auto* entry = s.outgoing[i].front()) {
                if (!s.h2g[i]->write(entry->data.data(), entry->data.size())) break;
                (void)s.outgoing[i].take();
            }
        }
        if (s.timing.command("CHIMAERA_INGRESS") != "OK INGRESS")
            throw std::runtime_error("m5ops admission command rejected");
        s.timing.start(interval);
        s.timing.wait();
        for (unsigned pass = 0;; ++pass) {
            if (pass == 1024) throw std::runtime_error("m5ops paused egress failed to drain");
            std::istringstream response(s.timing.command("CHIMAERA_EGRESS"));
            std::string marker, extra;
            std::uint64_t remaining;
            if (!(response >> marker >> remaining) || marker != "EGRESS" || (response >> extra))
                throw std::runtime_error("m5ops egress command rejected");
            bool capacity_blocked = false, progress = false;
            for (unsigned i = 0; i < 2; ++i) {
                chimaera_mailbox::Mailbox::View view{};
                while (s.g2h[i]->peek(view)) {
                    if (s.incoming[i].size() >= s.pendingLimit ||
                        view.size > egress_bytes - s.incoming[i].bytes()) {
                        // Retain the accepted frame in its ring. Guest sends
                        // eventually see EAGAIN through the bounded model queue,
                        // while the other lane keeps independent credits.
                        capacity_blocked = true;
                        break;
                    }
                    auto bytes = Message::for_overwrite(view.size);
                    if (view.size) std::memcpy(bytes.data(), view.data, view.size);
                    if (lane(bytes) != i) throw std::runtime_error("m5ops packet crossed the wrong lane");
                    // Validate capacity and acquire host ownership BEFORE
                    // returning this accepted record's ring slot to gem5.
                    s.incoming[i].submit(std::move(bytes));
                    s.g2h[i]->pop();
                    progress = true;
                }
            }
            if (!remaining || (capacity_blocked && !progress)) break;
        }
        return {};
    } catch (const std::exception& e) {
        s.failure = e.what();
        return {ControllerState::failed, s.failure};
    }
}
ControllerResult M5HostController::stop() {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    try {
        s.timing.shutdown();
        s.stopped = true;
        return {ControllerState::stopped, {}};
    } catch (const std::exception& e) { return {ControllerState::failed, e.what()}; }
}
} // namespace chimaera
