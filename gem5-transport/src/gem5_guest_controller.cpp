#include <chimaera/gem5_controller.hpp>
#include <chimaera/guest_transport.hpp>
#include "controller_protocol.hpp"

#include <thread>

namespace chimaera {
struct Gem5GuestController::Impl {
    GuestTransport transport;
    DataProducer& producer;
    DataConsumer& consumer;
    std::optional<control::Packet> pending;
    std::optional<std::uint64_t> epoch;
    Duration poll{std::chrono::milliseconds(1)};
    std::string failure;
    Impl(DataProducer& p, DataConsumer& c) : producer(p), consumer(c) {}
};
Gem5GuestController::Gem5GuestController(DataProducer& producer, DataConsumer& consumer)
    : impl_(std::make_unique<Impl>(producer, consumer)) {}
Gem5GuestController::~Gem5GuestController() = default;

ControllerResult Gem5GuestController::run_next() {
    auto& s = *impl_;
    if (!s.failure.empty()) return {ControllerState::failed, s.failure};
    try {
        if (s.pending) s.epoch = s.pending->epoch;
        while (true) {
            control::Packet reply{control::Kind::reply};
            if (s.pending) {
                reply = std::move(*s.pending);
                s.pending.reset();
            } else {
                control::send(s.transport, {control::Kind::poll, s.epoch.value_or(0),
                                           Duration{}, s.epoch ? control::collect(s.producer)
                                                               : control::Batch{}});
                reply = control::receive(s.transport, control::Kind::reply);
            }
            if (reply.poll.count() <= 0 ||
                (s.epoch && reply.epoch < *s.epoch))
                throw std::runtime_error("invalid host controller interval");
            // KVM can execute past workbegin until its global exit is serviced.
            // Before the first host step, exchange only empty bootstrap polls; never
            // invoke application callbacks during this startup window.
            if (reply.epoch == 0) {
                if (!reply.messages.empty())
                    throw std::runtime_error("application data in bootstrap reply");
                std::this_thread::sleep_for(reply.poll);
                continue;
            }
            if (s.epoch && reply.epoch != *s.epoch) {
                s.pending = std::move(reply);
                return {}; // Next interval's callbacks belong to the next call.
            }
            s.epoch = reply.epoch;
            s.poll = reply.poll;
            for (auto& message : reply.messages) s.consumer.submit(std::move(message));
            // This sleep runs in the guest OS, so it stops when gem5 is paused.
            std::this_thread::sleep_for(s.poll);
        }
    } catch (const std::exception& error) {
        s.failure = error.what();
        return {ControllerState::failed, s.failure};
    }
}
} // namespace chimaera
