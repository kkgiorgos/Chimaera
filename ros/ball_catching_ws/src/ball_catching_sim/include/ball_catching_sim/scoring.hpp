#pragma once
#include <cmath>

namespace ball_catching {
// Sphere containment against the polygonal cavity in cup-local coordinates.
inline bool contained(double x, double y, double z, double radius, double depth,
                      double ballRadius, double tolerance = 0.002) {
  if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) ||
      z < ballRadius - tolerance || z >= depth - ballRadius + tolerance) return false;
  for (int i = 0; i < 20; ++i) {
    const double angle = i * 2 * std::acos(-1.) / 20;
    if (x * std::cos(angle) + y * std::sin(angle) > radius - 0.004 - ballRadius + tolerance)
      return false;
  }
  return true;
}

class Retention {
 public:
  bool Update(bool inside, double time, double duration) {
    if (!inside) { since_ = -1; return false; }
    if (since_ < 0) since_ = time;
    return time - since_ >= duration;
  }
 private:
  double since_{-1};
};
}
