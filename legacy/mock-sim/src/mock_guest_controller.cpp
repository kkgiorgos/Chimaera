#include <mock_sim/mock_controller.hpp>
#include <mock_sim/mock_guest_transport.hpp>
#include "controller_protocol.hpp"
#include <limits>
#include <csignal>
#include <unistd.h>
#include <thread>

namespace mock_sim {
struct MockGuestController::Impl {
    std::optional<MockGuestTransport> transport;
    DataProducer& producer;
    DataConsumer& consumer;
    bool initialized{false};
    bool stopped{false};
    std::optional<std::string> failure;
    Impl(std::string endpoint, DataProducer& p, DataConsumer& c)
        : transport(std::in_place, std::move(endpoint)), producer(p), consumer(c) {}
};
MockGuestController::MockGuestController(std::string endpoint, DataProducer& producer, DataConsumer& consumer)
    : impl_(std::make_unique<Impl>(std::move(endpoint), producer, consumer)) {}
MockGuestController::~MockGuestController() = default;
ControllerResult MockGuestController::run_next() {
    auto& s = *impl_;
    if (s.failure) return {ControllerState::failed, *s.failure};
    if (s.stopped) return {ControllerState::stopped, {}};
    try {
        if (!s.initialized) {
            detail::number(*s.transport, detail::magic);
            s.initialized = true;
            if (::kill(::getpid(), SIGSTOP) < 0) throw std::runtime_error("cannot pause guest");
        }
        // This code can only execute once the host has resumed our process.
        if (detail::number(*s.transport) != detail::magic) throw std::runtime_error("controller protocol mismatch");
        const auto command = detail::number(*s.transport);
        if (command == 0) {
            s.stopped = true;
            detail::number(*s.transport, 0);
            return {ControllerState::stopped, {}};
        }
        if (command != 1) throw std::runtime_error("invalid controller command");
        const auto raw_interval = detail::number(*s.transport);
        const auto raw_poll = detail::number(*s.transport);
        if (raw_interval > static_cast<std::uint64_t>(std::numeric_limits<Duration::rep>::max()) ||
            raw_poll > static_cast<std::uint64_t>(std::numeric_limits<Duration::rep>::max()))
            throw std::runtime_error("invalid controller duration");
        const Duration interval(raw_interval), poll(raw_poll);
        if (!detail::valid(interval, poll)) throw std::runtime_error("invalid polling interval");
        const auto start = std::chrono::steady_clock::now();
        auto incoming = detail::receive_batch(*s.transport);
        for (Duration elapsed{}; elapsed < interval; elapsed += poll) {
            std::this_thread::sleep_until(start + elapsed);
            detail::deliver(std::exchange(incoming, {}), s.consumer);
            detail::send_batch(*s.transport, detail::collect(s.producer));
        }
        std::this_thread::sleep_until(start + interval);
        detail::number(*s.transport, 2);
        // Nothing below executes until the host resumes the whole process.
        // In particular, no receive, acknowledgement, callback, or return to
        // caller happens during the stopped portion of the sync boundary.
        if (::kill(::getpid(), SIGSTOP) < 0) throw std::runtime_error("cannot pause guest");
        return {};
    } catch (const std::exception& error) {
        s.failure = error.what();
        s.transport.reset();
        return {ControllerState::failed, *s.failure};
    }
}
} // namespace mock_sim
