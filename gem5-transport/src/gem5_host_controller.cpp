#include <chimaera/gem5_controller.hpp>
#include <chimaera/host_transport.hpp>
#include "controller_protocol.hpp"

#include <mutex>
#include <thread>

namespace chimaera {
struct Gem5HostController::Impl {
    HostTransport transport;
    Gem5TimingController& timing;
    DataProducer& producer;
    DataConsumer& consumer;
    std::mutex mutex;
    control::Batch incoming, outgoing;
    Duration poll{std::chrono::milliseconds(1)};
    std::uint64_t epoch{};
    std::string failure;
    bool stopped{false};
    std::thread worker;

    Impl(Gem5TimingController& t, DataProducer& p, DataConsumer& c,
         std::string g2h, std::string h2g)
        : transport(std::move(g2h), std::move(h2g)), timing(t), producer(p), consumer(c),
          worker([this] { serve(); }) {}
    ~Impl() { close(); }
    void close() {
        transport.cancel();
        if (worker.joinable()) worker.join();
    }
    void serve() {
        try {
            while (true) {
                auto request = control::receive(transport, control::Kind::poll);
                control::Packet reply{control::Kind::reply};
                {
                    std::lock_guard lock(mutex);
                    control::append(incoming, std::move(request.messages));
                    reply.epoch = epoch;
                    reply.poll = poll;
                    reply.messages = std::exchange(outgoing, {});
                }
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

Gem5HostController::Gem5HostController(Gem5TimingController& timing, DataProducer& producer,
                                     DataConsumer& consumer, std::string g2h, std::string h2g)
    : impl_(std::make_unique<Impl>(timing, producer, consumer, std::move(g2h), std::move(h2g))) {}
Gem5HostController::~Gem5HostController() = default;

ControllerResult Gem5HostController::step(Duration interval, Duration poll) {
    auto& s = *impl_;
    if (s.stopped) return {ControllerState::stopped, {}};
    if (!control::valid(interval, poll))
        return {ControllerState::failed, "require 0 < poll <= interval <= 1 hour, at most 100000 polls"};
    try {
        {
            std::lock_guard lock(s.mutex);
            if (!s.failure.empty()) throw std::runtime_error(s.failure);
        }
        auto outgoing = control::collect(s.producer);
        {
            std::lock_guard lock(s.mutex);
            control::append(s.outgoing, std::move(outgoing));
            s.poll = poll;
            ++s.epoch;
        }
        s.timing.start(interval);
        s.timing.wait();
        control::Batch incoming;
        {
            std::lock_guard lock(s.mutex);
            if (!s.failure.empty()) throw std::runtime_error(s.failure);
            incoming = std::exchange(s.incoming, {});
        }
        for (auto& message : incoming) s.consumer.submit(std::move(message));
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
