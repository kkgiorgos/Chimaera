#pragma once

#include <mock_sim/controller.hpp>
#include <memory>

namespace mock_sim {

class MockTimingController final : public TimingController {
public:
    void start(Duration interval) override;
    void wait() override;
private:
    std::chrono::steady_clock::time_point deadline_{};
    bool running_{false};
};

// Dependencies must outlive the controller. No concurrent calls are supported.
// Endpoint constructors own the corresponding mock transport.
// The guest MUST be a separate process: it uses SIGSTOP at sync boundaries.
// The host owns that process's execution lifecycle once connected. Call stop()
// for orderly shutdown; a failed/destroyed active host terminates its guest.
class MockHostController final : public HostController {
public:
    MockHostController(std::string endpoint, TimingController& timing,
                       DataProducer& producer, DataConsumer& consumer);
    ~MockHostController() override;
    ControllerResult step(Duration interval, Duration poll_interval) override;
    ControllerResult stop() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

class MockGuestController final : public GuestController {
public:
    MockGuestController(std::string endpoint, DataProducer& producer, DataConsumer& consumer);
    ~MockGuestController() override;
    ControllerResult run_next() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace mock_sim
