#pragma once
#include <cmath>
#include <optional>
#include <stdexcept>

namespace ball_catching
{
// Sphere containment against the polygonal cavity in cup-local coordinates.
inline bool contained(
  double x, double y, double z, double radius, double depth, double ballRadius,
  double tolerance = 0.002)
{
  if (
    !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) || z < ballRadius - tolerance ||
    z >= depth - ballRadius + tolerance)
    return false;
  for (int i = 0; i < 20; ++i) {
    const double angle = i * 2 * std::acos(-1.) / 20;
    if (x * std::cos(angle) + y * std::sin(angle) > radius - 0.004 - ballRadius + tolerance)
      return false;
  }
  return true;
}

struct Evaluation
{
  bool success;
  double containedDuration;
  std::optional<double> captureTime;
};

class DeadlineScore
{
public:
  explicit DeadlineScore(double duration) : duration_(duration)
  {
    if (!std::isfinite(duration) || duration <= 0)
      throw std::invalid_argument("duration must be finite and positive");
  }
  std::optional<Evaluation> Update(bool inside, double time, double launchTime)
  {
    if (inside) {
      if (!since_) since_ = time;
    } else {
      since_.reset();
    }
    if (time - launchTime + 1e-9 < duration_) return {};
    return Evaluation{inside, since_ ? time - *since_ : 0., since_};
  }

private:
  double duration_;
  std::optional<double> since_;
};
}  // namespace ball_catching
