#include <mock_sim/mock_controller.hpp>
#include <mock_sim/mock_host_transport.hpp>
#include "controller_protocol.hpp"
#include "guest_process.hpp"

namespace mock_sim {
struct MockHostController::Impl {
    std::optional<MockHostTransport> transport;
    detail::GuestProcess guest;
    TimingController& timing;
    DataProducer& producer;
    DataConsumer& consumer;
    bool stopped{false};
    std::optional<std::string> failure;
    Impl(std::string endpoint, TimingController& t, DataProducer& p, DataConsumer& c)
        : transport(std::in_place, std::move(endpoint)), timing(t), producer(p), consumer(c) {}
    ~Impl() { close(); }
    void close() {
        transport.reset();
        if (!stopped) guest.terminate();
    }
    void initialize() {
        if (guest.attached()) return;
        // Bootstrap precedes simulation execution. Credentials come from the
        // kernel, not a PID supplied by the controller protocol peer.
        if (detail::number(*transport) != detail::magic)
            throw std::runtime_error("controller protocol mismatch");
        guest.attach(transport->peer_process_id());
        guest.wait_paused();
    }
};
MockHostController::MockHostController(std::string endpoint, TimingController& timing,
                                     DataProducer& producer, DataConsumer& consumer)
    : impl_(std::make_unique<Impl>(std::move(endpoint), timing, producer, consumer)) {}
MockHostController::~MockHostController() = default;

ControllerResult MockHostController::step(Duration interval, Duration poll) {
    auto& s = *impl_;
    if (s.failure) return {ControllerState::failed, *s.failure};
    if (s.stopped) return {ControllerState::stopped, {}};
    if (!detail::valid(interval, poll))
        return {ControllerState::failed, "require 0 < poll <= interval <= 1 hour and at most 100000 polls"};
    try {
        s.initialize();
        auto outgoing = detail::collect(s.producer);
        detail::number(*s.transport, detail::magic);
        detail::number(*s.transport, 1); // Run interval.
        detail::number(*s.transport, interval.count());
        detail::number(*s.transport, poll.count());
        // Only small control frames are queued while paused. Large payloads
        // are transferred after resume, so socket capacity cannot deadlock us.
        s.timing.start(interval);
        s.guest.resume();
        detail::send_batch(*s.transport, outgoing);
        detail::Batch incoming;
        std::size_t bytes = 0;
        for (Duration elapsed{}; elapsed < interval; elapsed += poll) {
            auto batch = detail::receive_batch(*s.transport);
            for (auto& message : batch) {
                bytes += message.size();
                if (bytes > detail::max_bytes || incoming.size() >= detail::max_batch)
                    throw std::runtime_error("guest interval exceeds 1024 messages or 64 MiB");
                incoming.push_back(std::move(message));
            }
        }
        if (detail::number(*s.transport) != 2) throw std::runtime_error("guest did not stop");
        // The completion marker is emitted BEFORE stopping. Confirm the actual
        // kernel stop before invoking timing/boundary handlers.
        s.guest.wait_paused();
        s.timing.wait();
        detail::deliver(std::move(incoming), s.consumer);
        return {};
    } catch (const std::exception& error) {
        s.failure = error.what();
        s.close();
        return {ControllerState::failed, *s.failure};
    }
}
ControllerResult MockHostController::stop() {
    auto& s = *impl_;
    if (s.failure) return {ControllerState::failed, *s.failure};
    if (s.stopped) return {ControllerState::stopped, {}};
    try {
        s.initialize();
        detail::number(*s.transport, detail::magic);
        detail::number(*s.transport, 0);
        s.guest.resume(); // Explicit lifecycle resume for shutdown, no callbacks.
        if (detail::number(*s.transport) != 0) throw std::runtime_error("missing stop acknowledgement");
        s.stopped = true;
        return {ControllerState::stopped, {}};
    } catch (const std::exception& error) {
        s.failure = error.what();
        s.close();
        return {ControllerState::failed, *s.failure};
    }
}
} // namespace mock_sim
