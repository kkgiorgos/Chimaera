#include <chimaera/wall_clock_pacer.hpp>

#include <cmath>
#include <stdexcept>

namespace chimaera {
WallClockPacer::WallClockPacer(double ratio) : ratio_(ratio) {
    if (!std::isfinite(ratio) || ratio <= 0)
        throw std::invalid_argument("wall-clock ratio must be finite and positive");
}

std::chrono::nanoseconds WallClockPacer::delay(std::uint64_t ticks) const {
    const long double simulated = static_cast<long double>(ticks) / 1'000'000'000'000.L;
    const long double elapsed = std::chrono::duration<long double>(Clock::now() - started_).count();
    const long double remaining = simulated / ratio_ - elapsed;
    if (remaining <= 0) return std::chrono::nanoseconds::zero();
    if (remaining >= 1) return std::chrono::seconds(1);
    return std::chrono::nanoseconds(static_cast<std::int64_t>(std::ceil(remaining * 1'000'000'000.L)));
}

RateReport WallClockPacer::report(std::uint64_t ticks) {
    if (ticks < last_ticks_) throw std::invalid_argument("simulation progress moved backwards");
    const auto now = Clock::now();
    const double total_wall = std::chrono::duration<double>(now - started_).count();
    const double window_wall = std::chrono::duration<double>(now - last_report_).count();
    const double total_sim = static_cast<double>(ticks) / 1e12;
    const double window_sim = static_cast<double>(ticks - last_ticks_) / 1e12;
    last_report_ = now;
    last_ticks_ = ticks;
    return {total_sim, total_wall, window_wall > 0 ? window_sim / window_wall : 0,
            total_wall > 0 ? total_sim / total_wall : 0};
}
} // namespace chimaera
