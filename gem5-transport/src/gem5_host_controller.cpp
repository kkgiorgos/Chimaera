#include <chimaera/gem5_controller.hpp>
#include <chimaera/host_transport.hpp>
#include "controller_protocol.hpp"

#include <mutex>
#include <thread>

namespace chimaera {
struct Gem5HostController::Impl {
    HostTransport transport;
    Gem5TimingController& timing;
    std::mutex mutex;
    control::ReceivedQueue incoming, ready;
    control::OutgoingQueue outgoing;
    const Duration poll;
    bool started{false};
    std::string failure;
    bool stopped{false};
    std::thread worker;

    Impl(Gem5TimingController& t, Duration p, std::string g2h, std::string h2g)
        : transport(std::move(g2h), std::move(h2g)), timing(t), poll(p) {
        if (poll.count() <= 0 || poll > std::chrono::hours(1))
            throw std::invalid_argument("poll interval must be positive and at most one hour");
        worker = std::thread([this] { serve(); });
    }
    ~Impl() { close(); }
    void close() {
        transport.cancel();
        if (worker.joinable()) worker.join();
    }
    void serve() {
        try {
            // Keep bootstrap responsive while KVM services its workbegin exit.
            // Sending configuration completes startup; only then accept batches.
            while (true) {
                if (control::receive(transport) != control::startup_request())
                    throw std::runtime_error("controller protocol mismatch");
                bool ready;
                {
                    std::lock_guard lock(mutex);
                    ready = started;
                }
                control::send(transport, control::encode_startup(ready ? poll : Duration{}));
                if (ready) break;
            }
            while (true) {
                auto request = control::decode(control::receive(transport));
                control::Batch outgoing_batch;
                {
                    std::lock_guard lock(mutex);
                    incoming.append(std::move(request));
                    outgoing_batch = outgoing.drain();
                }
                // Assemble the outgoing packet outside the mutex.
                const auto reply = control::encode(outgoing_batch);
                // A paused guest may be between either pair of transport ops.
                // Leave this exchange pending until gem5 resumes; never join it
                // at an interval boundary.
                control::send(transport, reply);
            }
        } catch (const std::exception& error) {
            std::lock_guard lock(mutex);
            if (failure.empty()) failure = error.what();
        }
    }
};

Gem5HostController::Gem5HostController(Gem5TimingController& timing, Duration poll,
                                       std::string g2h, std::string h2g)
    : impl_(std::make_unique<Impl>(timing, poll, std::move(g2h), std::move(h2g))) {}
Gem5HostController::~Gem5HostController() = default;

void Gem5HostController::submit(Message data, std::string_view key) {
    auto& s = *impl_;
    std::lock_guard lock(s.mutex);
    if (s.stopped) throw std::runtime_error("host controller stopped");
    if (!s.failure.empty()) throw std::runtime_error(s.failure);
    s.outgoing.submit(std::move(data), key);
}
std::optional<Message> Gem5HostController::take() {
    return impl_->ready.take();
}

ControllerResult Gem5HostController::step(Duration interval) {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    if (!control::valid(interval, s.poll))
        return {ControllerState::failed, "require 0 < poll <= interval <= 1 hour, at most 100000 polls"};
    try {
        {
            std::lock_guard lock(s.mutex);
            if (!s.failure.empty()) throw std::runtime_error(s.failure);
            s.started = true;
        }
        s.timing.start(interval);
        s.timing.wait();
        control::Batch received;
        {
            std::lock_guard lock(s.mutex);
            if (!s.failure.empty()) throw std::runtime_error(s.failure);
            received = s.incoming.drain();
        }
        s.ready.append(std::move(received));
        return {};
    } catch (const std::exception& error) {
        // Retain the I/O worker until destruction/stop: the simulator could
        // still be completing an m5 operation after a timing failure.
        std::lock_guard lock(s.mutex);
        s.failure = error.what();
        return {ControllerState::failed, s.failure};
    }
}

ControllerResult Gem5HostController::stop() {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    try {
        s.timing.shutdown();
        s.close();
        s.stopped = true;
        return {ControllerState::stopped, {}};
    } catch (const std::exception& error) {
        return {ControllerState::failed, error.what()};
    }
}
} // namespace chimaera
