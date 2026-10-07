#include <Eigen/Dense>
#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "ball_catching_robot/core.hpp"

namespace ball_catching
{
Vec6 propagateBall(const Vec6 & initial, double interval, double drag)
{
  if (
    !initial.allFinite() || !std::isfinite(interval) || interval < 0 || !std::isfinite(drag) ||
    drag < 0)
    throw std::invalid_argument("Invalid flight state");
  Vec6 state = initial;
  const int steps = std::max(1, static_cast<int>(std::ceil(interval / .005)));
  const double dt = interval / steps;
  auto rate = [drag](const Vec6 & s) {
    Vec6 r;
    r.head<3>() = s.tail<3>();
    r.tail<3>() = gravity - drag * s.tail<3>().norm() * s.tail<3>();
    return r;
  };
  for (int i = 0; i < steps; ++i) {
    const Vec6 a = rate(state), b = rate(state + dt * a / 2), c = rate(state + dt * b / 2),
               d = rate(state + dt * c);
    state += dt * (a + 2 * b + 2 * c + d) / 6;
  }
  return state;
}
std::optional<Vec3> DragFilter::predict(double time) const
{
  if (!state_ || !std::isfinite(time) || time < *time_) return {};
  return propagateBall(state_->head<6>(), time - *time_, (*state_)[6]).head<3>();
}
std::optional<Vec6> DragFilter::observe(
  const Vec3 & point, double time, const Eigen::Matrix3d & noise)
{
  if (!point.allFinite() || !std::isfinite(time) || (time_ && time <= *time_)) return {};
  // Detect a floor reversal from consecutive visual displacements. The
  // restitution, bounce count and contact messages are unavailable here.
  const double observedVz =
    previousObservation_ && time_ ? (point.z() - previousObservation_->z()) / (time - *time_) : 0.;
  const bool reversal = state_ && previousObservation_ && time_ && time - *time_ < .08 &&
                        point.z() < .35 && (*state_)[5] < -1. && observedVz > .5 &&
                        previousVerticalVelocity_ > .5;
  if ((time_ && time - *time_ > .2) || reversal) {
    history_.clear();
    state_.reset();
    ++resets_;
  }
  previousObservation_ = point;
  previousVerticalVelocity_ = observedVz;
  if (!state_) {
    history_.emplace_back(time, point);
    time_ = time;
    if (history_.size() < 6 || time - history_.front().first < .035) return {};
    const double origin = history_.front().first;
    Eigen::MatrixXd a(history_.size(), 2), b(history_.size(), 3);
    for (size_t i = 0; i < history_.size(); ++i) {
      const double t = history_[i].first - origin;
      a.row(i) << 1., t;
      b.row(i) = (history_[i].second - .5 * gravity * t * t).transpose();
    }
    const Eigen::Matrix<double, 2, 3> fit = a.colPivHouseholderQr().solve(b);
    Vec7 s;
    const double dt = time - origin;
    s.head<3>() = fit.row(0).transpose() + fit.row(1).transpose() * dt + .5 * gravity * dt * dt;
    s.segment<3>(3) = fit.row(1).transpose() + gravity * dt;
    s[6] = 0.;
    state_ = s;
    // A new track must not inherit position/velocity correlations from the
    // previous impact or occlusion. Use the current stereo measurement scale.
    covariance_.setZero();
    covariance_.topLeftCorner<3, 3>() = 4 * noise;
    covariance_.diagonal().segment<3>(3).setConstant(4.);
    covariance_(6, 6) = .0025;
    history_.clear();
  } else {
    double remaining = time - *time_;
    Vec7 predicted = *state_;
    Mat7 covariance = covariance_;
    while (remaining > 1e-10) {
      const double dt = std::min(.005, remaining);
      const Vec3 v = predicted.segment<3>(3);
      const double speed = v.norm(), k = predicted[6];
      const Eigen::Matrix3d derivative =
        -k * (speed * Eigen::Matrix3d::Identity() + v * v.transpose() / std::max(speed, 1e-9));
      Mat7 transition = Mat7::Identity();
      transition.block<3, 3>(0, 3) = dt * Eigen::Matrix3d::Identity() + .5 * dt * dt * derivative;
      transition.block<3, 3>(3, 3) += dt * derivative;
      transition.block<3, 1>(0, 6) = -.5 * dt * dt * speed * v;
      transition.block<3, 1>(3, 6) = -dt * speed * v;
      predicted.head<6>() = propagateBall(predicted.head<6>(), dt, k);
      covariance = (transition * covariance * transition.transpose()).eval();
      covariance.diagonal() +=
        dt * (Vec7() << .00001, .00001, .00001, .02, .02, .02, .000001).finished();
      remaining -= dt;
    }
    const Vec3 innovation = point - predicted.head<3>();
    if (innovation.norm() > .6) {
      ++resets_;
      state_.reset();
      history_ = {{time, point}};
      time_ = time;
      return {};
    }
    const Eigen::Matrix3d residual = covariance.topLeftCorner<3, 3>() + noise;
    const Eigen::Matrix<double, 7, 3> gain =
      residual.ldlt().solve(covariance.topRows<3>()).transpose();
    state_ = predicted + gain * innovation;
    (*state_)[6] = std::clamp((*state_)[6], 0., .15);
    Mat7 correction = Mat7::Identity();
    correction.leftCols<3>() -= gain;
    covariance_ =
      correction * covariance * correction.transpose() + gain * noise * gain.transpose();
    time_ = time;
  }
  return state_->head<6>();
}
}  // namespace ball_catching
