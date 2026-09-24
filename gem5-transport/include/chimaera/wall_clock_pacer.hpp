#pragma once

#include <chrono>
#include <cstdint>

namespace chimaera {

struct RateReport {
    double simulated_seconds{};
    double wall_seconds{};
    double achieved_ratio{}; // Actual simulation seconds / wall second since last report.
    double average_ratio{}; // Same quantity over the whole paced run.
};

// Construct after startup/boot. Pass actual elapsed ticks, not nominal intervals.
// All methods are called on the controller thread. This class does not sleep;
// the application can service input while waiting for the returned delay.
class WallClockPacer {
public:
    explicit WallClockPacer(double ratio);
    [[nodiscard]] double target_ratio() const noexcept { return ratio_; }
    // Delay until the actual simulation progress matches the requested ratio.
    // Capped at one second to permit responsive input and periodic reporting.
    [[nodiscard]] std::chrono::nanoseconds delay(std::uint64_t elapsed_ticks) const;
    [[nodiscard]] RateReport report(std::uint64_t elapsed_ticks);
private:
    using Clock = std::chrono::steady_clock;
    double ratio_;
    Clock::time_point started_{Clock::now()};
    Clock::time_point last_report_{started_};
    std::uint64_t last_ticks_{};
};

} // namespace chimaera
