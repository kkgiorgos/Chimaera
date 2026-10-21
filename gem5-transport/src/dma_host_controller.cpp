#include <chimaera/dma_controller.hpp>
#include <chimaera/outgoing_queue.hpp>
#include <gem5/chimaera_mailbox.hh>
#include "controller_protocol.hpp"
#include "dma_protocol.hpp"

namespace chimaera {
struct DmaHostController::Impl {
    Gem5TimingController& timing;
    chimaera_mailbox::Mailbox h2g, g2h;
    control::OutgoingQueue outgoing;
    control::ReceivedQueue incoming;
    bool started{}, stopped{};
    std::string failure;
    Impl(Gem5TimingController& t, std::string input, std::string output, std::size_t pending)
        : timing(t), h2g(std::move(input), true, 16), g2h(std::move(output), true, 16),
          outgoing(pending) {}
};
DmaHostController::DmaHostController(Gem5TimingController& timing, std::string h2g,
        std::string g2h, std::size_t pending)
    : impl_(std::make_unique<Impl>(timing, std::move(h2g), std::move(g2h), pending)) {}
DmaHostController::~DmaHostController() = default;
void DmaHostController::submit(Message data, std::string_view key) {
    auto& s = *impl_;
    if (s.stopped) throw std::runtime_error("DMA host stopped");
    if (!s.failure.empty()) throw std::runtime_error(s.failure);
    if (data.size() > CHDMA_MAX_BYTES - dma::header_bytes)
        throw std::length_error("DMA frame exceeds 32 MiB including framing");
    s.outgoing.submit(std::move(data), key);
}
std::optional<Message> DmaHostController::take() { return impl_->incoming.take(); }
ControllerResult DmaHostController::step(Duration interval) {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    if (!s.failure.empty()) return {ControllerState::failed, s.failure};
    if (interval.count() <= 0 || interval > std::chrono::hours(1))
        return {ControllerState::failed, "DMA interval must be positive and at most one hour"};
    try {
        if (!s.started) {
            if (!s.h2g.write(dma::startup, sizeof(dma::startup) - 1))
                throw std::runtime_error("DMA startup mailbox unexpectedly full");
            s.started = true;
        }
        while (true) {
            const auto layout = dma::layout(s.outgoing.entries());
            if (!layout.count) break;
            auto* output = s.h2g.reserve(layout.size());
            if (!output) break;
            dma::encode(reinterpret_cast<std::byte*>(output), layout, s.outgoing.entries());
            s.h2g.commit(layout.size());
            for (std::size_t i = 0; i < layout.count; ++i) (void)s.outgoing.take();
        }
        if (s.timing.command("CHIMAERA_INGRESS") != "OK INGRESS")
            throw std::runtime_error("DMA admission command rejected");
        s.timing.start(interval);
        s.timing.wait();
        // Copy while paused; never retain a mailbox view after returning its
        // slot to the simulator. Guest-side publication keeps a registered loan.
        chimaera_mailbox::Mailbox::View view{};
        control::Batch received;
        while (s.g2h.peek(view)) {
            auto bytes = Message::for_overwrite(view.size);
            if (view.size) std::memcpy(bytes.data(), view.data, view.size);
            auto messages = dma::decode(std::move(bytes));
            for (auto& message : messages) received.push_back(std::move(message));
            s.g2h.pop();
        }
        s.incoming.append(std::move(received));
        return {};
    } catch (const std::exception& e) {
        s.failure = e.what();
        return {ControllerState::failed, s.failure};
    }
}
ControllerResult DmaHostController::stop() {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    try {
        s.timing.shutdown();
        s.stopped = true;
        return {ControllerState::stopped, {}};
    } catch (const std::exception& e) { return {ControllerState::failed, e.what()}; }
}
} // namespace chimaera
