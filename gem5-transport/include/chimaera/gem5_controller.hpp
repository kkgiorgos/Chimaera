#pragma once

#include <chimaera/controller.hpp>
#include <chimaera/guest_transport.hpp>
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
    // Wait for a paused server before starting simulation intervals.
    void wait_until_ready(std::chrono::seconds timeout = std::chrono::seconds(300),
                          const std::function<bool()>& cancelled = {});
    // Actual progress since the first step's start, at the fixed 1 THz frequency.
    [[nodiscard]] std::uint64_t elapsed_ticks() const noexcept;
    void shutdown(); // QUIT: terminate gem5 while paused, without running guest code.
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Owns HostTransport and its I/O worker. Timing must outlive the controller.
// stop terminates gem5; destruction alone cancels local I/O without advancing it.
class Gem5HostController final : public HostController {
public:
    // Configure polling once; startup sends this duration to the guest.
    Gem5HostController(Gem5TimingController& timing,
                       Duration poll_interval = std::chrono::milliseconds(1),
                       std::string guest_to_host = "/tmp/chimaera_g2h.sock",
                       std::string host_to_guest = "/tmp/chimaera_h2g.sock");
    ~Gem5HostController() override;
    void submit(Message data, std::string_view coalescing_key = {}) override;
    std::optional<Message> take() override;
    ControllerResult step(Duration interval) override;
    ControllerResult stop() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Owns GuestTransport; address mode requires an application-owned libm5 mapping.
// Instruction mode runs on a simulated CPU without a mapping. For KVM boot,
// address_bootstrap keeps using address ops until startup confirms host readiness
// after the workbegin CPU switch; keep the mapping alive throughout run_next. Polling
// sleeps use the guest OS clock. run_next performs one batch exchange after
// startup, allowing the application to submit/take data between polls.
class Gem5GuestController final : public GuestController {
public:
    explicit Gem5GuestController(GuestM5Ops ops = GuestM5Ops::address,
                                 bool address_bootstrap = false);
    ~Gem5GuestController() override;
    void submit(Message data, std::string_view coalescing_key = {}) override;
    std::optional<Message> take() override;
    ControllerResult run_next() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace chimaera
