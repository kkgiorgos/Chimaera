#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace wall_follow
{
constexpr double nan = std::numeric_limits<double>::quiet_NaN();
constexpr double pi = 3.14159265358979323846;
struct Parameters
{
  double control_hz = 20, target_distance = .8, speed = .35, kp = 1.8, heading_gain = 2,
         max_yaw_rate = 1.2, front_stop = .65, scan_timeout = .3, sector_start = -110,
         sector_end = -40, fit_threshold = .04;
  int64_t beam_stride = 1, min_points = 6;
  void validate() const
  {
    for (double v :
         {control_hz, target_distance, speed, max_yaw_rate, front_stop, scan_timeout,
          fit_threshold})
      if (!std::isfinite(v) || v <= 0)
        throw std::invalid_argument("positive finite parameters required");
    if (
      !std::isfinite(kp) || kp < 0 || !std::isfinite(heading_gain) || heading_gain < 0 ||
      control_hz < 1 || control_hz > 500 || !std::isfinite(sector_start) ||
      !std::isfinite(sector_end) || sector_start < -175 || sector_end > -5 ||
      sector_start >= sector_end || beam_stride < 1 || min_points < 3)
      throw std::invalid_argument("invalid controller parameters");
  }
};
struct Command
{
  double speed = 0, yaw = 0, distance = nan, heading = nan;
  std::string state = "waiting_scan";
  double clearance = nan;
};
struct Point
{
  double x, y;
};
inline Command command(
  const std::vector<float> & ranges, double angle_min, double increment, double range_min,
  double range_max, const Parameters & p)
{
  Command out;
  out.clearance = std::numeric_limits<double>::infinity();
  bool front_known = false;
  std::vector<Point> points;
  size_t valid_sector = 0;
  for (size_t i = 0; i < ranges.size(); ++i) {
    double r = ranges[i], a = angle_min + i * increment;
    bool valid = std::isfinite(r) && r >= range_min && r <= range_max;
    if (std::abs(a) < 20 * pi / 180) {
      front_known |= valid || (std::isinf(r) && r > 0);
      if (valid) out.clearance = std::min(out.clearance, r);
    }
    if (
      valid && a >= p.sector_start * pi / 180 && a <= p.sector_end * pi / 180 &&
      valid_sector++ % p.beam_stride == 0)
      points.push_back({r * std::cos(a), r * std::sin(a)});
  }
  if (!front_known) {
    out.state = "invalid_front";
    return out;
  }
  if (out.clearance < p.front_stop) {
    out.state = "corner";
    out.yaw = p.max_yaw_rate;
    return out;
  }
  out.state = "lost_wall";
  if (points.size() < static_cast<size_t>(p.min_points)) return out;
  std::vector<size_t> best;
  size_t n = std::min<size_t>(10, points.size());
  for (size_t i = 0; i < n; ++i)
    for (size_t j = i + 1; j < n; ++j) {
      auto first = points[i * (points.size() - 1) / (n - 1)],
           second = points[j * (points.size() - 1) / (n - 1)];
      double dx = second.x - first.x, dy = second.y - first.y, length = std::hypot(dx, dy);
      if (length < .05) continue;
      std::vector<size_t> inliers;
      for (size_t k = 0; k < points.size(); ++k)
        if (
          std::abs(-(points[k].x - first.x) * dy + (points[k].y - first.y) * dx) / length <=
          p.fit_threshold)
          inliers.push_back(k);
      if (inliers.size() > best.size()) best = std::move(inliers);
    }
  if (best.size() < static_cast<size_t>(p.min_points)) return out;
  double x = 0, y = 0, xx = 0, xy = 0, yy = 0;
  for (auto i : best) {
    x += points[i].x;
    y += points[i].y;
  }
  x /= best.size();
  y /= best.size();
  for (auto i : best) {
    double dx = points[i].x - x, dy = points[i].y - y;
    xx += dx * dx;
    xy += dx * dy;
    yy += dy * dy;
  }
  // Principal eigenvector of the 2x2 covariance, oriented toward positive x.
  out.heading = .5 * std::atan2(2 * xy, xx - yy);
  out.distance = std::abs(std::cos(out.heading) * y - std::sin(out.heading) * x);
  out.yaw = std::clamp(
    -p.kp * (out.distance - p.target_distance) + p.heading_gain * out.heading, -p.max_yaw_rate,
    p.max_yaw_rate);
  out.speed = p.speed * std::max(.2, 1 - std::abs(out.yaw) / p.max_yaw_rate);
  out.state = "tracking";
  return out;
}
}  // namespace wall_follow
