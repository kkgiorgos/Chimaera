#include <mock_sim/mock_controller.hpp>
#include <stdexcept>
#include <thread>

namespace mock_sim {
void MockTimingController::start(Duration interval) {
    if (running_) throw std::logic_error("an interval is already running");
    if (interval <= Duration::zero() || interval > std::chrono::hours(1))
        throw std::invalid_argument("interval must be positive and at most one hour");
    deadline_ = std::chrono::steady_clock::now() + interval;
    running_ = true;
}
void MockTimingController::wait() {
    if (!running_) throw std::logic_error("no interval is running");
    std::this_thread::sleep_until(deadline_);
    running_ = false;
}
} // namespace mock_sim
