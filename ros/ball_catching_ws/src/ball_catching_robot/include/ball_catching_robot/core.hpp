#pragma once
#include <Eigen/Core>
#include <functional>
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
Vec7 uprightHome();

class Arm
{
public:
  explicit Arm(const std::string & urdf);
  ~Arm();
  KDL::Frame pose(const Vec7 & q) const;
  std::optional<Vec7> inverse(
    const Vec3 & position, const KDL::Rotation & rotation, const Vec7 & seed, int iterations = 35,
    int * iterationsUsed = nullptr) const;
  Vec7 forces(const Vec7 & q, const Vec7 & dq) const;
  Mat7 mass(const Vec7 & q) const;
  Eigen::Matrix<double, 6, 7> jacobian(const Vec7 & q) const;
  std::pair<Vec7, Vec7> speedLimits(const Vec7 & q) const;
  void validatePose(const Vec7 & q) const;
  void validateHome(const Vec7 & q) const;
  std::vector<std::string> names;
  Vec7 lower, upper, velocity, effort;
  Vec7 acceleration = Vec7::Constant(10.);

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

// Keep following the active command until a replacement's future start time.
class TrajectorySchedule
{
public:
  void replace(const std::vector<Quintic> & segments, double now);
  MotionState sample(double time, const MotionState & fallback) const;
  bool empty() const { return commands_.empty(); }

private:
  std::vector<std::vector<Quintic>> commands_;
};

// Follow the visually estimated incoming velocity, then brake with continuous
// joint position, velocity and acceleration while keeping the cup upright.
std::optional<std::vector<Quintic>> cupCatchTrajectory(
  const Arm & arm, double start, double flightTime, const MotionState & initial,
  const Vec7 & target, const Vec3 & incomingVelocity, double matchingSpeed, double brakeTime);

struct InterceptPlan
{
  std::vector<Quintic> segments;
  Vec3 point = Vec3::Zero(), velocity = Vec3::Zero();
  int inverseSolutions{0}, inverseCalls{0}, inverseIterations{0}, candidates{0};
  bool interrupted{false};
};

// Pure search shared by the ROS node and recorded-input/timing regression tests.
// Ball position, velocity and desired arm state are expressed at start.
class CupInterceptPlanner
{
public:
  explicit CupInterceptPlanner(const Arm & arm);
  InterceptPlan search(
    double start, const MotionState & initial, const Vec7 & feedback, const Vec3 & position,
    const Vec3 & velocity, double radius, const std::function<bool()> & shouldStop = {}) const;

private:
  const Arm & arm_;
  Vec7 seed_;
  KDL::Rotation rotation_;
  double height_;
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
  std::optional<Vec6> observe(
    const Vec3 & point, double time,
    const Eigen::Matrix3d & observationNoise = .000025 * Eigen::Matrix3d::Identity());
  const Mat6 & covariance() const { return covariance_; }

private:
  struct Observation
  {
    double time;
    Vec3 point;
    Eigen::Matrix3d noise;
  };
  std::vector<Observation> history_;
  std::optional<double> time_;
  std::optional<Vec6> state_;
  Mat6 covariance_ = Mat6::Zero();
};

}  // namespace ball_catching
