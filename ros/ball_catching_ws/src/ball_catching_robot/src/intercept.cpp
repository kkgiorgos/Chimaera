#include <Eigen/Dense>
#include <geometry_msgs/msg/vector3_stamped.hpp>
#include <limits>
#include <nav_msgs/msg/odometry.hpp>
#include <trajectory_msgs/msg/joint_trajectory.hpp>

#include "ball_catching_robot/runtime.hpp"

namespace ball_catching
{
class Intercept : public rclcpp::Node
{
public:
  Intercept()
  : Node("ball_intercept"),
    arm_(readFile(declare_parameter<std::string>("robot_file", ""))),
    record_(declare_parameter<std::string>("output", ""), "interception", this),
    processingClock_(*this)
  {
    const auto home =
      jointVector(declare_parameter<std::vector<double>>("initial_pose", values(uprightHome())));
    arm_.validateHome(home);
    home_ = home;
    const auto pose = arm_.pose(home);
    rotation_ = arm_.pose(inverseSeed_).M;
    height_ = pose.p.z();
    commands_ =
      create_publisher<trajectory_msgs::msg::JointTrajectory>("/robot/joint_trajectory", 1);
    joints_ = create_subscription<sensor_msgs::msg::JointState>(
      "/robot/joint_states", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) {
        feedback_.update(*msg, arm_.names);
      });
    observations_ = create_subscription<nav_msgs::msg::Odometry>(
      "/robot/ball_state", rclcpp::SensorDataQoS(),
      [this](nav_msgs::msg::Odometry::ConstSharedPtr msg) {
        if (msg->header.frame_id != "world") return;
        const auto & p = msg->pose.pose.position;
        const auto & v = msg->twist.twist.linear;
        Ball ball{seconds(msg->header.stamp), Vec3(p.x, p.y, p.z), Vec3(v.x, v.y, v.z)};
        if (ball.position.allFinite() && ball.velocity.allFinite()) ball_ = ball;
      });
    geometry_ = create_subscription<geometry_msgs::msg::Vector3Stamped>(
      "/robot/ball_geometry", rclcpp::SensorDataQoS(),
      [this](geometry_msgs::msg::Vector3Stamped::ConstSharedPtr msg) {
        if (msg->vector.x > .005 && msg->vector.x < .15)
          radius_ = radius_ ? .2 * msg->vector.x + .8 * (*radius_) : msg->vector.x;
      });
    const double hz = declare_parameter("planner_hz", 30.);
    planInterval_ = declare_parameter("plan_interval", .06);
    if (!std::isfinite(hz) || hz <= 0 || !std::isfinite(planInterval_) || planInterval_ < 0)
      throw std::invalid_argument("Invalid planner frequency or search interval");
    timer_ = timer(*this, 1. / hz, [this] { plan(); });
  }

private:
  struct Ball
  {
    double time;
    Vec3 position, velocity;
  };
  void plan()
  {
    const auto stamp = now();
    const double time = stamp.seconds();
    if (
      !feedback_.valid || !ball_ || !radius_ || time < holdUntil_ ||
      (cupInterceptTime_ > 0 && time >= cupInterceptTime_ - .025))
      return;
    const double age = time - ball_->time;
    if (age < 0 || age > .1 || time - feedback_.time > .05 || time - lastPlan_ < planInterval_)
      return;
    const Vec3 position = ball_->position + ball_->velocity * age + .5 * gravity * age * age;
    const Vec3 velocity = ball_->velocity + gravity * age;
    const auto cup = arm_.pose(feedback_.q).p;
    if (
      position.z() < cup.z() + .025 &&
      (position.head<2>() - Eigen::Vector2d(cup.x(), cup.y())).norm() < .13) {
      // Visual entry triggers holding; privileged scoring never enters control.
      holdUntil_ = time + 2.;
      return;
    }
    const auto started = processingClock_.now();
    lastPlan_ = time;
    const MotionState initial = desired(time);
    std::vector<Quintic> best;
    Vec3 bestPoint, bestVelocity;
    int inverseSolutions = 0;
    double score = std::numeric_limits<double>::infinity();
    for (double height :
         {height_, height_ + .05, height_ - .05, height_ + .10, height_ - .10, height_ + .15,
          height_ - .15, height_ + .20, height_ - .20}) {
      const double discriminant =
        velocity.z() * velocity.z() + 2 * 9.81 * (position.z() - height - *radius_ - .005);
      if (discriminant < 0) continue;
      const double dt = (velocity.z() + std::sqrt(discriminant)) / 9.81;
      if (dt <= .07 || dt >= 1.2) continue;
      Vec3 point = position + velocity * dt + .5 * gravity * dt * dt;
      point.z() = height;
      const double distance = point.head<2>().norm();
      if (distance <= .12 || distance >= 1.10 || height <= .20 || height >= 1.30) continue;
      auto target = arm_.inverse(point, rotation_, feedback_.q, 120);
      // A nearly straight arm is an IK singularity. A second numerical seed
      // selects a bent-elbow solution without changing the physical start pose.
      if (!target) target = arm_.inverse(point, rotation_, inverseSeed_, 120);
      if (!target) continue;
      ++inverseSolutions;
      const Vec3 incoming = velocity + gravity * dt;
      for (double matchingSpeed : {1.5, 1., .7, .35, .2, .1}) {
        for (double brakeTime : {.25, .4, .6}) {
          const auto candidate = cupCatchTrajectory(
            arm_, time, dt, initial, *target, incoming, matchingSpeed, brakeTime);
          if (!candidate) continue;
          const auto catchState = candidate->front().sample(time + dt);
          const Vec3 cupVelocity = (arm_.jacobian(*target) * catchState.dq).head<3>();
          const double cost =
            (*target - initial.q).norm() + .10 * dt + .8 * (incoming - cupVelocity).norm();
          if (cost < score) {
            score = cost;
            best = *candidate;
            bestPoint = point;
            bestVelocity = cupVelocity;
          }
        }
      }
    }
    if (best.empty()) {
      record_.write(
        {{"time", time},
         {"capture_time", ball_->time},
         {"status", "no_feasible_intercept"},
         {"inverse_solutions", static_cast<double>(inverseSolutions)},
         {"planning_wall_seconds", processingClock_.elapsed(started)}});
      return;
    }
    publish(best);
    cupInterceptTime_ = best.back().start;
    record_.write(
      {{"time", time},
       {"capture_time", ball_->time},
       {"status", "planned"},
       {"target", values(bestPoint)},
       {"target_joints", values(best.front().sample(cupInterceptTime_).q)},
       {"intercept_time", cupInterceptTime_},
       {"cup_velocity", values(bestVelocity)},
       {"braking_start_time", best.back().start},
       {"braking_end_time", best.back().start + best.back().duration},
       {"planning_wall_seconds", processingClock_.elapsed(started)}});
  }
  void publish(const std::vector<Quintic> & segments)
  {
    trajectory_msgs::msg::JointTrajectory msg;
    msg.header.stamp = rclcpp::Time(static_cast<int64_t>(segments.front().start * 1e9));
    msg.joint_names = arm_.names;
    auto append = [&](const MotionState & s, double offset) {
      trajectory_msgs::msg::JointTrajectoryPoint p;
      p.positions = values(s.q);
      p.velocities = values(s.dq);
      p.accelerations = values(s.ddq);
      p.time_from_start = rclcpp::Duration::from_seconds(offset);
      msg.points.push_back(p);
    };
    append(segments.front().sample(segments.front().start), 0.);
    for (const auto & segment : segments)
      append(
        segment.sample(segment.start + segment.duration),
        segment.start + segment.duration - segments.front().start);
    commands_->publish(msg);
    trajectories_ = segments;
  }
  MotionState desired(double time) const
  {
    MotionState s{home_, Vec7::Zero(), Vec7::Zero()};
    for (const auto & segment : trajectories_) {
      s = segment.sample(time);
      if (time <= segment.start + segment.duration) break;
    }
    return s;
  }
  Arm arm_;
  Recorder record_;
  ProcessingClock processingClock_;
  Feedback feedback_;
  Vec7 home_;
  // A bent numerical seed avoids the singularity at the physical vertical home.
  const Vec7 inverseSeed_ = (Vec7() << 0., -.7853981633974483, 0., -2.356194490192345, 0.,
                             1.5707963267948966, .7853981633974483)
                              .finished();
  KDL::Rotation rotation_;
  double height_, lastPlan_{-1}, holdUntil_{-1}, planInterval_{.06};
  std::optional<Ball> ball_;
  std::optional<double> radius_;
  std::vector<Quintic> trajectories_;
  double cupInterceptTime_{-1};
  rclcpp::Publisher<trajectory_msgs::msg::JointTrajectory>::SharedPtr commands_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr observations_;
  rclcpp::Subscription<geometry_msgs::msg::Vector3Stamped>::SharedPtr geometry_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}  // namespace ball_catching
int main(int argc, char ** argv)
{
  return ball_catching::run<ball_catching::Intercept>(argc, argv);
}
