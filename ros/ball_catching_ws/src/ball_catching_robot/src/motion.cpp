#include "ball_catching_robot/core.hpp"
#include <Eigen/Dense>
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace ball_catching {
Quintic::Quintic(double startTime, double interval, const MotionState &initial, const Vec7 &target)
    : start(startTime), duration(interval) {
  if (!std::isfinite(start) || !std::isfinite(duration) || duration <= 0 ||
      !initial.q.allFinite() || !initial.dq.allFinite() || !initial.ddq.allFinite() || !target.allFinite())
    throw std::invalid_argument("Invalid quintic endpoints or duration");
  coefficients_.row(0) = initial.q.transpose();
  coefficients_.row(1) = initial.dq.transpose();
  coefficients_.row(2) = initial.ddq.transpose() / 2;
  const double t = duration;
  Eigen::Matrix3d a;
  a << t*t*t, std::pow(t, 4), std::pow(t, 5), 3*t*t, 4*t*t*t, 5*std::pow(t, 4), 6*t, 12*t*t, 20*t*t*t;
  Eigen::Matrix<double, 3, 7> b;
  b.row(0) = (target - initial.q - initial.dq*t - initial.ddq*t*t/2).transpose();
  b.row(1) = (-initial.dq - initial.ddq*t).transpose();
  b.row(2) = -initial.ddq.transpose();
  coefficients_.bottomRows<3>() = a.partialPivLu().solve(b);
}
MotionState Quintic::sample(double now) const {
  const double t = std::clamp(now - start, 0., duration);
  Eigen::Matrix<double, 1, 6> p, v, a;
  p << 1, t, t*t, t*t*t, std::pow(t, 4), std::pow(t, 5);
  v << 0, 1, 2*t, 3*t*t, 4*t*t*t, 5*std::pow(t, 4);
  a << 0, 0, 2, 6*t, 12*t*t, 20*t*t*t;
  return {(p*coefficients_).transpose(), (v*coefficients_).transpose(), (a*coefficients_).transpose()};
}
bool Quintic::feasible(const Arm &arm) const {
  for (int i = 0; i <= 30; ++i) {
    const auto s = sample(start + duration*i/30.);
    const auto [lo, hi] = arm.speedLimits(s.q);
    if (!s.q.allFinite() || !s.dq.allFinite() || !s.ddq.allFinite() ||
        (s.q.array() < arm.lower.array() + .02).any() || (s.q.array() > arm.upper.array() - .02).any() ||
        (s.dq.array() < lo.array()*.9).any() || (s.dq.array() > hi.array()*.9).any() ||
        (s.ddq.cwiseAbs().array() > arm.acceleration.array()).any()) return false;
  }
  return true;
}
}
