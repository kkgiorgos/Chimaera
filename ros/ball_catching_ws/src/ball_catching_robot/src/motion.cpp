#include "ball_catching_robot/core.hpp"
#include <Eigen/Dense>
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace ball_catching {
Quintic::Quintic(
  double startTime, double interval, const MotionState & initial, const Vec7 & target)
: Quintic(startTime, interval, initial, MotionState{target, Vec7::Zero(), Vec7::Zero()})
{
}
Quintic::Quintic(
  double startTime, double interval, const MotionState & initial, const MotionState & target)
: start(startTime), duration(interval)
{
  if (
    !std::isfinite(start) || !std::isfinite(duration) || duration <= 0 || !initial.q.allFinite() ||
    !initial.dq.allFinite() || !initial.ddq.allFinite() || !target.q.allFinite() ||
    !target.dq.allFinite() || !target.ddq.allFinite())
    throw std::invalid_argument("Invalid quintic endpoints or duration");
  coefficients_.row(0) = initial.q.transpose();
  coefficients_.row(1) = initial.dq.transpose();
  coefficients_.row(2) = initial.ddq.transpose() / 2;
  const double t = duration;
  Eigen::Matrix3d a;
  a << t*t*t, std::pow(t, 4), std::pow(t, 5), 3*t*t, 4*t*t*t, 5*std::pow(t, 4), 6*t, 12*t*t, 20*t*t*t;
  Eigen::Matrix<double, 3, 7> b;
  b.row(0) = (target.q - initial.q - initial.dq * t - initial.ddq * t * t / 2).transpose();
  b.row(1) = (target.dq - initial.dq - initial.ddq * t).transpose();
  b.row(2) = (target.ddq - initial.ddq).transpose();
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
std::optional<std::vector<Quintic>> cupCatchTrajectory(
  const Arm & arm, double start, double flightTime, const MotionState & initial,
  const Vec7 & target, const Vec3 & incomingVelocity, double matchingSpeed, double brakeTime)
{
  if (arm.gripper || !incomingVelocity.allFinite() || incomingVelocity.norm() < 1e-9 ||
      !std::isfinite(matchingSpeed) || matchingSpeed <= 0 ||
      !std::isfinite(brakeTime) || brakeTime <= 0)
    throw std::invalid_argument("Invalid cup velocity or braking duration");
  const auto jacobian = arm.jacobian(target);
  Vec6 twist = Vec6::Zero();
  twist.head<3>() = incomingVelocity * std::min(1., matchingSpeed / incomingVelocity.norm());
  Vec7 dq = jacobian.transpose() *
            (jacobian * jacobian.transpose() + .0001 * Mat6::Identity()).ldlt().solve(twist);
  const auto [lo, hi] = arm.speedLimits(target);
  double scale = 1.;
  for (int i = 0; i < 7; ++i) {
    const double limit = dq[i] >= 0 ? hi[i] : -lo[i];
    scale = std::min(scale, .75 * limit / std::max(std::abs(dq[i]), 1e-9));
  }
  dq *= scale;
  if ((jacobian * dq).head<3>().dot(incomingVelocity.normalized()) < .05) return {};
  const MotionState caught{target, dq, Vec7::Zero()};
  Quintic approach(start, flightTime, initial, caught);
  Quintic brake(start + flightTime, brakeTime, caught, (target + dq * brakeTime / 2).eval());
  if (!approach.feasible(arm) || !brake.feasible(arm)) return {};
  const double initialTilt = std::acos(std::clamp(arm.pose(initial.q).M(2, 2), -1., 1.));
  for (const auto & segment : {approach, brake}) {
    for (int i = 0; i <= 30; ++i) {
      const auto pose = arm.pose(segment.sample(segment.start + segment.duration * i / 30.).q);
      // Upright throughout approach and braking; keep the rim safely above the
      // floor. Bounds apply to commanded motion, not idealized physics poses.
      const double tilt = segment.start == start ? .08 + initialTilt * (1. - i / 30.) : .08;
      if (pose.M(2, 2) < std::cos(tilt) || pose.p.z() < .25) return {};
    }
  }
  return std::vector<Quintic>{approach, brake};
}
}
