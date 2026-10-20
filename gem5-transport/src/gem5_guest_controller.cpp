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
    Duration poll{}; // Zero until startup supplies the immutable polling interval.
    std::string failure;
    Impl(GuestM5Ops ops, bool bootstrap)
        : transport(ops), address_bootstrap(bootstrap) {}
};
Gem5GuestController::Gem5GuestController(GuestM5Ops ops, bool address_bootstrap)
    : impl_(std::make_unique<Impl>(ops, address_bootstrap)) {}
Gem5GuestController::~Gem5GuestController() = default;

void Gem5GuestController::reconnect() {
    impl_->poll = Duration{};
}

void Gem5GuestController::submit(Message data, std::string_view key) {
    if (!impl_->failure.empty()) throw std::runtime_error(impl_->failure);
    impl_->outgoing.submit(std::move(data), key);
}
std::optional<Message> Gem5GuestController::take() {
    return impl_->incoming.take();
}

ControllerResult Gem5GuestController::run_next() {
    return run_next({});
}

ControllerResult Gem5GuestController::run_next(
        const std::function<void(Duration)>& before_exchange) {
    auto& s = *impl_;
    if (!s.failure.empty()) return {ControllerState::failed, s.failure};
    try {
        const bool initialized = s.poll.count() > 0;
        if (!initialized) {
            auto& bootstrap = s.address_bootstrap ? s.bootstrap_transport : s.transport;
            do {
                control::send(bootstrap, control::startup_request());
                s.poll = control::decode_startup(control::receive(bootstrap));
                // KVM may execute beyond workbegin before its exit/CPU switch
                // completes. Keep submitted data local until the host is ready.
                if (s.poll.count() == 0) std::this_thread::sleep_for(control::bootstrap_delay);
            } while (s.poll.count() == 0);
        }
        // Output from the previous receive/callback phase must not wait through
        // another poll. With an executor hook, new ROS output also wakes this
        // exchange directly, avoiding periodic subscription service.
        const auto wait = initialized && s.outgoing.size() == 0 ? s.poll : Duration{};
        if (before_exchange) before_exchange(wait);
        else if (wait.count() > 0) std::this_thread::sleep_for(wait);
        // Once initialized, both directions carry only batches of messages.
        control::send_batch(s.transport, s.outgoing.drain());
        s.incoming.append(control::receive_batch(s.transport));
        return {};
    } catch (const std::exception& error) {
        s.failure = error.what();
        return {ControllerState::failed, s.failure};
    }
}
} // namespace chimaera
