#pragma once
#include <Eigen/Core>
#include <kdl/frames.hpp>
#include <memory>
#include <opencv2/core.hpp>
#include <optional>
#include <string>
#include <vector>

namespace ball_catching
{
using Vec3 = Eigen::Vector3d;
using Vec6 = Eigen::Matrix<double, 6, 1>;
using Vec7 = Eigen::Matrix<double, 7, 1>;
using Mat6 = Eigen::Matrix<double, 6, 6>;
using Mat7 = Eigen::Matrix<double, 7, 7>;
inline const Vec3 gravity(0., 0., -9.81);

class Arm
{
public:
  explicit Arm(const std::string & urdf);
  ~Arm();
  KDL::Frame pose(const Vec7 & q) const;
  std::optional<Vec7> inverse(
    const Vec3 & position, const KDL::Rotation & rotation, const Vec7 & seed,
    int iterations = 35) const;
  Vec7 forces(const Vec7 & q, const Vec7 & dq) const;
  Mat7 mass(const Vec7 & q) const;
  Eigen::Matrix<double, 6, 7> jacobian(const Vec7 & q) const;
  std::pair<Vec7, Vec7> speedLimits(const Vec7 & q) const;
  void validatePose(const Vec7 & q) const;
  std::vector<std::string> names;
  Vec7 lower, upper, velocity, effort;
  Vec7 acceleration = Vec7::Constant(10.);
  bool gripper{false};

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

struct MotionState
{
  Vec7 q, dq, ddq;
};
class Quintic
{
public:
  Quintic(double start, double duration, const MotionState & initial, const Vec7 & target);
  Quintic(double start, double duration, const MotionState & initial, const MotionState & target);
  MotionState sample(double now) const;
  bool feasible(const Arm & arm) const;
  double start, duration;

private:
  Eigen::Matrix<double, 6, 7> coefficients_;
};

struct Circle
{
  double u, v, radius;
};
struct Detection
{
  Vec3 point;
  double radius;
};
std::vector<Circle> candidates(const cv::Mat & rgb);
class Stereo
{
public:
  Stereo(double focal, double cx, double cy, double baseline, const Vec3 & origin);
  std::optional<Vec3> point(const Circle & left, const Circle & right) const;
  Eigen::Matrix3d observationNoise(const Vec3 & point) const;
  std::optional<Detection> detect(
    const cv::Mat & left, const cv::Mat & right, const std::optional<Vec3> & predicted = {}) const;

private:
  double focal_, cx_, cy_, baseline_;
  Vec3 origin_;
};

class BallFilter
{
public:
  std::optional<Vec3> predict(double time) const;
  std::optional<Vec6> observe(const Vec3 & point, double time);
  const Mat6 & covariance() const { return covariance_; }

private:
  std::vector<std::pair<double, Vec3>> history_;
  std::optional<double> time_;
  std::optional<Vec6> state_;
  Mat6 covariance_ = Mat6::Zero();
};

// Drag is inferred from images, never copied from simulator material settings.
Vec6 propagateBall(const Vec6 & state, double interval, double drag);
class DragFilter
{
public:
  std::optional<Vec3> predict(double time) const;
  std::optional<Vec6> observe(
    const Vec3 & point, double time,
    const Eigen::Matrix3d & noise = (.0001 * Eigen::Matrix3d::Identity()).eval());
  Mat6 covariance() const { return covariance_.topLeftCorner<6, 6>(); }
  double drag() const { return state_ ? (*state_)[6] : 0.; }
  unsigned resets() const { return resets_; }

private:
  std::vector<std::pair<double, Vec3>> history_;
  std::optional<double> time_;
  std::optional<Vec7> state_;
  Mat7 covariance_ = Mat7::Zero();
  std::optional<Vec3> previousObservation_;
  double previousVerticalVelocity_{0};
  unsigned resets_{0};
};
}  // namespace ball_catching
