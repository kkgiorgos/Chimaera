#pragma once

#include <chimaera/transport.hpp>
#include <chrono>
#include <functional>
#include <optional>
#include <string_view>

namespace chimaera {

using Duration = std::chrono::nanoseconds;

// Applications push outgoing data and pull received data. Calls to a controller
// are serialized by the application. The concrete guest controller additionally
// offers an explicit executor hook before an exchange; transport I/O never
// invokes application callbacks.
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

// Optional credit-based handoff. The coordinator calls the receiver before
// removing an accepted incoming record. A rejected lane retains its FIFO while
// independent lanes can continue. Implementations bound all retained storage.
class BackpressureController {
public:
    virtual ~BackpressureController() = default;
    // False retains the caller's ownership and changes no outgoing queue.
    virtual bool try_submit(Message data) = 0;
    // The receiver gets an explicit shared view and must treat its bytes as
    // immutable. Refusal retains the original storage in this controller.
    virtual std::size_t transfer_received(
        const std::function<bool(Message)>& receiver) = 0;
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
    // Wait up to the configured polling duration, then exchange one batch.
    // Already queued output may bypass the idle wait.
    // The first call completes startup and exchanges a batch without a polling wait.
    // External scheduling pauses this call along with all other guest code.
    [[nodiscard]] virtual ControllerResult run_next() = 0;
};

} // namespace chimaera
