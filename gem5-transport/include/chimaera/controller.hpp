#pragma once

#include <chimaera/transport.hpp>
#include <chrono>
#include <optional>

namespace chimaera {

using Duration = std::chrono::nanoseconds;
using Message = std::vector<std::byte>;

// Application-owned handlers. All calls are serialized on the controller thread.
class DataProducer {
public:
    virtual ~DataProducer() = default;
    // Remove the next queued message, or return nullopt when no data is ready.
    virtual std::optional<Message> take() = 0;
};

class DataConsumer {
public:
    virtual ~DataConsumer() = default;
    virtual void submit(Message data) = 0;
};

class TimingController {
public:
    virtual ~TimingController() = default;
    // Start the configured simulator(s) for this duration.
    virtual void start(Duration interval) = 0;
    // Return only after the configured simulator(s) have completed the interval.
    virtual void wait() = 0;
};

enum class ControllerState { completed, stopped, failed };
struct ControllerResult {
    ControllerState state{ControllerState::completed};
    std::string message;
    [[nodiscard]] bool ok() const noexcept { return state == ControllerState::completed; }
};

class HostController {
public:
    virtual ~HostController() = default;
    // Collect queued host data before resume; deliver guest data after stopping.
    [[nodiscard]] virtual ControllerResult step(Duration interval, Duration poll_interval) = 0;
    [[nodiscard]] virtual ControllerResult stop() = 0;
};

class GuestController {
public:
    virtual ~GuestController() = default;
    // Execute an interval under external scheduling. No code (including this
    // call's return) runs while paused. Completion may therefore be observed by
    // the caller only on the following resume. Keep calling until stopped/failed.
    [[nodiscard]] virtual ControllerResult run_next() = 0;
};

} // namespace chimaera
