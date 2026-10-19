#include <Eigen/Dense>
#include <cmath>
#include <limits>

#include "ball_catching_robot/core.hpp"

namespace ball_catching
{
CupInterceptPlanner::CupInterceptPlanner(const Arm & arm) : arm_(arm)
{
  seed_ << 0., -.7853981633974483, 0., -2.356194490192345, 0., 1.5707963267948966,
    .7853981633974483;
  rotation_ = arm_.pose(seed_).M;
  height_ = arm_.pose(uprightHome()).p.z();
}

InterceptPlan CupInterceptPlanner::search(
  double start, const MotionState & initial, const Vec7 & feedback, const Vec3 & position,
  const Vec3 & velocity, double radius, const std::function<bool()> & shouldStop) const
{
  InterceptPlan result;
  auto interrupted = [&] {
    result.interrupted = shouldStop && shouldStop();
    return result.interrupted;
  };
  double score = std::numeric_limits<double>::infinity();
  for (double height :
       {height_ - .20, height_ - .15, height_ - .10, height_ - .05, height_, height_ + .05,
        height_ + .10, height_ + .15, height_ + .20}) {
    // Try the longer-flight, lower catch heights first, retaining a feasible
    // result if the budget expires before all alternatives have been scored.
    if (interrupted()) return result;
    const double discriminant =
      velocity.z() * velocity.z() + 2 * 9.81 * (position.z() - height - radius - .005);
    if (discriminant < 0) continue;
    const double dt = (velocity.z() + std::sqrt(discriminant)) / 9.81;
    if (dt <= .07 || dt >= 1.2) continue;
    Vec3 point = position + velocity * dt + .5 * gravity * dt * dt;
    point.z() = height;
    const double distance = point.head<2>().norm();
    if (distance <= .12 || distance >= 1.10 || height <= .20 || height >= 1.30) continue;
    auto inverse = [&](const Vec7 & seed) {
      int iterations = 0;
      // Retain the core solver's 35-iteration bound. Spending 120 iterations
      // per seed on unreachable heights blocked newer visual observations
      // for almost 100 ms in the Timing CPU baseline.
      auto target = arm_.inverse(point, rotation_, seed, 35, &iterations);
      ++result.inverseCalls;
      result.inverseIterations += iterations;
      return target;
    };
    auto target = inverse(feedback);
    if (interrupted()) return result;
    if (!target) {
      target = inverse(seed_);
      if (interrupted()) return result;
    }
    if (!target) continue;
    ++result.inverseSolutions;
    const Vec3 incoming = velocity + gravity * dt;
    for (double matchingSpeed : {1.5, 1., .7, .35, .2, .1}) {
      for (double brakeTime : {.25, .4, .6}) {
        if (interrupted()) return result;
        ++result.candidates;
        const auto candidate =
          cupCatchTrajectory(arm_, start, dt, initial, *target, incoming, matchingSpeed, brakeTime);
        if (!candidate) continue;
        const auto catchState = candidate->front().sample(start + dt);
        const Vec3 cupVelocity = (arm_.jacobian(*target) * catchState.dq).head<3>();
        const double cost =
          (*target - initial.q).norm() + .10 * dt + .8 * (incoming - cupVelocity).norm();
        if (cost < score) {
          score = cost;
          result.segments = *candidate;
          result.point = point;
          result.velocity = cupVelocity;
        }
      }
    }
  }
  return result;
}
}  // namespace ball_catching
