#include <chimaera/gem5_controller.hpp>
#include <chimaera/guest_transport.hpp>
#include "controller_protocol.hpp"

#include <thread>

namespace chimaera {
struct Gem5GuestController::Impl {
    GuestTransport transport;
    GuestTransport bootstrap_transport;
    bool address_bootstrap;
    control::OutgoingQueue outgoing;
    control::ReceivedQueue incoming;
    std::optional<std::uint64_t> epoch;
    Duration poll{std::chrono::milliseconds(1)};
    std::string failure;
    Impl(GuestM5Ops ops, bool bootstrap)
        : transport(ops), address_bootstrap(bootstrap) {}
};
Gem5GuestController::Gem5GuestController(GuestM5Ops ops, bool address_bootstrap)
    : impl_(std::make_unique<Impl>(ops, address_bootstrap)) {}
Gem5GuestController::~Gem5GuestController() = default;

void Gem5GuestController::submit(Message data, std::string_view key) {
    if (!impl_->failure.empty()) throw std::runtime_error(impl_->failure);
    impl_->outgoing.submit(std::move(data), key);
}
std::optional<Message> Gem5GuestController::take() {
    return impl_->incoming.take();
}

ControllerResult Gem5GuestController::run_next() {
    auto& s = *impl_;
    if (!s.failure.empty()) return {ControllerState::failed, s.failure};
    try {
        // Let the application deliver the previous reply before waiting for
        // another poll. This uses the guest OS clock and pauses with gem5.
        if (s.epoch) std::this_thread::sleep_for(s.poll);
        while (true) {
            // Retain submitted application data until bootstrap has completed.
            auto& transport = s.address_bootstrap && !s.epoch
                ? s.bootstrap_transport : s.transport;
            control::send(transport, {control::Kind::poll, s.epoch.value_or(0),
                                      Duration{}, s.epoch ? s.outgoing.drain() : control::Batch{}});
            auto reply = control::receive(transport, control::Kind::reply);
            if (reply.poll.count() <= 0 ||
                (s.epoch && reply.epoch < *s.epoch))
                throw std::runtime_error("invalid host controller interval");
            // KVM can execute past workbegin until its global exit is serviced.
            // Before the first host step, exchange only empty bootstrap polls; never
            // expose application data during this startup window.
            if (reply.epoch == 0) {
                if (!reply.messages.empty())
                    throw std::runtime_error("application data in bootstrap reply");
                std::this_thread::sleep_for(reply.poll);
                continue;
            }
            s.epoch = reply.epoch;
            s.poll = reply.poll;
            s.incoming.append(std::move(reply.messages));
            return {};
        }
    } catch (const std::exception& error) {
        s.failure = error.what();
        return {ControllerState::failed, s.failure};
    }
}
} // namespace chimaera
