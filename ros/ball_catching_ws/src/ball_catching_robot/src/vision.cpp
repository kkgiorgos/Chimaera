#include <Eigen/Dense>
#include <algorithm>
#include <cmath>
#include <limits>
#include <opencv2/imgproc.hpp>
#include <stdexcept>

#include "ball_catching_robot/core.hpp"

namespace ball_catching
{
std::vector<Circle> candidates(const cv::Mat & rgb)
{
  cv::Mat hsv, mask;
  cv::cvtColor(rgb, hsv, cv::COLOR_RGB2HSV);
  cv::inRange(hsv, cv::Scalar(25, 90, 80), cv::Scalar(85, 255, 255), mask);
  cv::morphologyEx(mask, mask, cv::MORPH_OPEN, cv::Mat::ones(3, 3, CV_8U));
  std::vector<std::vector<cv::Point>> contours;
  cv::findContours(mask, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
  std::vector<Circle> result;
  for (const auto & contour : contours) {
    const double area = cv::contourArea(contour), perimeter = cv::arcLength(contour, true);
    if (area < 6 || perimeter == 0 || 4 * std::acos(-1.) * area / (perimeter * perimeter) < .45)
      continue;
    cv::Point2f center;
    float radius;
    cv::minEnclosingCircle(contour, center, radius);
    result.push_back({center.x, center.y, radius});
  }
  return result;
}
Stereo::Stereo(double focal, double cx, double cy, double baseline, const Vec3 & origin)
: focal_(focal), cx_(cx), cy_(cy), baseline_(baseline), origin_(origin)
{
  if (
    !std::isfinite(focal) || focal <= 0 || !std::isfinite(baseline) || baseline <= 0 ||
    !std::isfinite(cx) || !std::isfinite(cy) || !origin.allFinite())
    throw std::invalid_argument("Invalid stereo calibration");
}
std::optional<Vec3> Stereo::point(const Circle & left, const Circle & right) const
{
  const double disparity = left.u - right.u;
  if (disparity < 1 || std::abs(left.v - right.v) > 3) return {};
  const double z = focal_ * baseline_ / disparity;
  // Optical (right, down, forward) to calibrated world (+x, -y, -z).
  return origin_ +
         Vec3(z, -(left.u - cx_) * z / focal_, -((left.v + right.v) / 2 - cy_) * z / focal_);
}
Eigen::Matrix3d Stereo::observationNoise(const Vec3 & point) const
{
  const Vec3 offset = point - origin_;
  if (!offset.allFinite() || offset.x() <= 0)
    throw std::invalid_argument("Visual point must be in front of the camera");
  const double depth = offset.x(), slope = depth * depth / (focal_ * baseline_);
  // Propagate independent 0.35 pixel centroid errors through stereo geometry.
  // Correlation matters for an offset camera: a depth error also moves y/z.
  Eigen::Matrix<double, 3, 4> j = Eigen::Matrix<double, 3, 4>::Zero();
  j(0, 0) = -slope;
  j(0, 1) = slope;
  j(1, 0) = -depth / focal_ + offset.y() / depth * j(0, 0);
  j(1, 1) = offset.y() / depth * j(0, 1);
  j(2, 0) = offset.z() / depth * j(0, 0);
  j(2, 1) = offset.z() / depth * j(0, 1);
  j(2, 2) = j(2, 3) = -depth / (2 * focal_);
  return .35 * .35 * j * j.transpose() + .000004 * Eigen::Matrix3d::Identity();
}
std::optional<Detection> Stereo::detect(
  const cv::Mat & left, const cv::Mat & right, const std::optional<Vec3> & predicted) const
{
  const auto l = candidates(left), r = candidates(right);
  std::optional<Detection> best;
  double score = std::numeric_limits<double>::infinity();
  for (const auto & a : l)
    for (const auto & b : r) {
      if (std::min(a.radius, b.radius) / std::max(a.radius, b.radius) < .65) continue;
      auto p = point(a, b);
      if (!p || !p->allFinite()) continue;
      const double candidate = predicted ? (*p - *predicted).norm() : -a.radius;
      if (candidate < score) {
        score = candidate;
        best = Detection{*p, (a.radius + b.radius) / 2 * ((*p)[0] - origin_[0]) / focal_};
      }
    }
  return best;
}
std::optional<Vec3> BallFilter::predict(double time) const
{
  if (!state_) return {};
  const double dt = time - *time_;
  return state_->head<3>() + state_->tail<3>() * dt + .5 * gravity * dt * dt;
}
std::optional<Vec6> BallFilter::observe(const Vec3 & point, double time)
{
  if (!point.allFinite() || !std::isfinite(time) || (time_ && time <= *time_)) return {};
  if (time_ && time - *time_ > .15) {
    history_.clear();
    state_.reset();
  }
  if (!state_) {
    history_.emplace_back(time, point);
    time_ = time;
    if (history_.size() < 5) return {};
    Eigen::MatrixXd a(history_.size(), 2), b(history_.size(), 3);
    for (size_t i = 0; i < history_.size(); ++i) {
      const double t = history_[i].first - time;
      a(i, 0) = 1;
      a(i, 1) = t;
      b.row(i) = (history_[i].second - .5 * gravity * t * t).transpose();
    }
    const Eigen::Matrix<double, 2, 3> fit = a.colPivHouseholderQr().solve(b);
    Vec6 initial;
    initial << fit.row(0).transpose(), fit.row(1).transpose();
    state_ = initial;
    covariance_.setZero();
    covariance_.diagonal() << .0001, .0001, .0001, .1, .1, .1;
  } else {
    const double dt = time - *time_;
    Mat6 transition = Mat6::Identity();
    transition.topRightCorner<3, 3>() = dt * Eigen::Matrix3d::Identity();
    Vec6 forcing;
    forcing << .5 * gravity * dt * dt, gravity * dt;
    const Vec6 predicted = transition * (*state_) + forcing;
    Mat6 covariance = transition * covariance_ * transition.transpose();
    covariance.diagonal() += (Vec6() << 1e-6, 1e-6, 1e-6, 1e-3, 1e-3, 1e-3).finished();
    const Vec3 innovation = point - predicted.head<3>();
    if (innovation.norm() > .25) {
      state_.reset();
      history_ = {{time, point}};
      time_ = time;
      return {};
    }
    const Eigen::Matrix3d residual =
      covariance.topLeftCorner<3, 3>() + .000025 * Eigen::Matrix3d::Identity();
    const Eigen::Matrix<double, 6, 3> gain =
      residual.ldlt().solve(covariance.topRows<3>()).transpose();
    state_ = predicted + gain * innovation;
    covariance_ = covariance - gain * covariance.topRows<3>();
    time_ = time;
  }
  return state_;
}
}  // namespace ball_catching
