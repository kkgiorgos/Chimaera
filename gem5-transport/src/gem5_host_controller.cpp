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
    control::Batch incoming, incoming_snapshot;
    std::size_t incoming_bytes{};
    control::ReceivedQueue ready;
    control::OutgoingQueue outgoing;
    Duration poll{std::chrono::milliseconds(1)};
    std::uint64_t epoch{};
    std::string failure;
    bool stopped{false};
    std::thread worker;

    Impl(Gem5TimingController& t, std::string g2h, std::string h2g)
        : transport(std::move(g2h), std::move(h2g)), timing(t) {
        // Reserve descriptor storage before starting the worker, so publishing
        // received batches does not allocate while holding the shared mutex.
        incoming.reserve(control::max_messages);
        incoming_snapshot.reserve(control::max_messages);
        worker = std::thread([this] { serve(); });
    }
    ~Impl() { close(); }
    void close() {
        transport.cancel();
        if (worker.joinable()) worker.join();
    }
    void serve() {
        try {
            control::OutgoingQueue detached;
            while (true) {
                auto request = control::receive(transport, control::Kind::poll);
                const auto request_bytes = control::bytes(request.messages);
                control::Packet reply{control::Kind::reply};
                {
                    std::lock_guard lock(mutex);
                    if (incoming.size() + request.messages.size() > control::max_messages ||
                        request_bytes > control::max_bytes - incoming_bytes)
                        throw std::runtime_error("controller queue limit exceeded");
                    for (auto& message : request.messages) incoming.push_back(std::move(message));
                    incoming_bytes += request_bytes;
                    reply.epoch = epoch;
                    reply.poll = poll;
                    // Never expose application data in a bootstrap reply.
                    if (epoch) outgoing.swap(detached);
                }
                // Batch allocation and key destruction happen outside the mutex.
                reply.messages = detached.drain();
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

Gem5HostController::Gem5HostController(Gem5TimingController& timing, std::string g2h, std::string h2g)
    : impl_(std::make_unique<Impl>(timing, std::move(g2h), std::move(h2g))) {}
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

ControllerResult Gem5HostController::step(Duration interval, Duration poll) {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    if (!control::valid(interval, poll))
        return {ControllerState::failed, "require 0 < poll <= interval <= 1 hour, at most 100000 polls"};
    try {
        {
            std::lock_guard lock(s.mutex);
            if (!s.failure.empty()) throw std::runtime_error(s.failure);
            s.poll = poll;
            ++s.epoch;
        }
        s.timing.start(interval);
        s.timing.wait();
        {
            std::lock_guard lock(s.mutex);
            if (!s.failure.empty()) throw std::runtime_error(s.failure);
            s.incoming_snapshot.swap(s.incoming);
            s.incoming_bytes = 0;
        }
        s.ready.append(std::move(s.incoming_snapshot));
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
