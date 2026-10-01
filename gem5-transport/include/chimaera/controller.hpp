#pragma once

#include <chimaera/transport.hpp>
#include <chrono>
#include <optional>
#include <string_view>

namespace chimaera {

using Duration = std::chrono::nanoseconds;
using Message = std::vector<std::byte>;

// Applications push outgoing data and pull received data. Calls to a controller
// are serialized by the application; no application callbacks are invoked.
class DataController {
public:
    virtual ~DataController() = default;
    // Queue an outgoing message. Empty keys preserve FIFO; a nonempty key
    // replaces pending messages with the same key. The controller copies the key.
    // Queue overflow throws without changing the queue.
    virtual void submit(Message data, std::string_view coalescing_key = {}) = 0;
    // Remove the next received message, or return nullopt when none is ready.
    virtual std::optional<Message> take() = 0;
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

class HostController : public DataController {
public:
    virtual ~HostController() = default;
    // Resume for an interval, then make decoded guest data available to take().
    [[nodiscard]] virtual ControllerResult step(Duration interval) = 0;
    [[nodiscard]] virtual ControllerResult stop() = 0;
};

class GuestController : public DataController {
public:
    virtual ~GuestController() = default;
    // Wait for the configured polling duration, then exchange one batch.
    // The first call completes startup and exchanges a batch without a polling wait.
    // External scheduling pauses this call along with all other guest code.
    [[nodiscard]] virtual ControllerResult run_next() = 0;
};

} // namespace chimaera
