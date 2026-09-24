#pragma once

#include <chimaera/controller.hpp>
#include <cstdint>
#include <functional>
#include <memory>

namespace chimaera {

// start sends a command without waiting for simulation completion. wait reads
// its reply. A single instance owns the timing session; calls are serialized.
// Actual tick drift is compensated in subsequent requests against the sum of
// nominal intervals. A step may request zero ticks if gem5 is already ahead.
class Gem5TimingController final : public TimingController {
public:
    explicit Gem5TimingController(std::string endpoint = "/tmp/chimaera_time.sock",
                                 std::chrono::seconds timeout = std::chrono::seconds(300));
    ~Gem5TimingController() override;
    void start(Duration interval) override;
    void wait() override;
    // Wait for a paused server before starting automatic pacing (boot excluded).
    void wait_until_ready(std::chrono::seconds timeout = std::chrono::seconds(300),
                          const std::function<bool()>& cancelled = {});
    // Actual progress since the first step's start, at the fixed 1 THz frequency.
    [[nodiscard]] std::uint64_t elapsed_ticks() const noexcept;
    void shutdown(); // QUIT: terminate gem5 while paused, without running guest code.
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Owns HostTransport and its I/O worker. Only the caller thread invokes
// producer/consumer callbacks. Dependencies must outlive the controller.
// stop terminates gem5; destruction alone cancels local I/O without advancing it.
class Gem5HostController final : public HostController {
public:
    Gem5HostController(Gem5TimingController& timing, DataProducer& producer,
                       DataConsumer& consumer,
                       std::string guest_to_host = "/tmp/chimaera_g2h.sock",
                       std::string host_to_guest = "/tmp/chimaera_h2g.sock");
    ~Gem5HostController() override;
    ControllerResult step(Duration interval, Duration poll_interval) override;
    ControllerResult stop() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Owns GuestTransport; the application still owns the libm5 mapping. Polling
// sleeps use the guest OS clock. run_next returns when a later host interval is
// observed, so completion is observed only after the next resume/poll.
class Gem5GuestController final : public GuestController {
public:
    Gem5GuestController(DataProducer& producer, DataConsumer& consumer);
    ~Gem5GuestController() override;
    ControllerResult run_next() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace chimaera
