#include <Eigen/Dense>
#include <geometry_msgs/msg/vector3_stamped.hpp>
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
    planner_(arm_),
    record_(declare_parameter<std::string>("output", ""), "interception", this),
    processingClock_(*this)
  {
    const auto home =
      jointVector(declare_parameter<std::vector<double>>("initial_pose", values(uprightHome())));
    arm_.validateHome(home);
    home_ = home;
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
    planningBudget_ = 1. / hz;
    timer_ = timer(*this, planningBudget_, [this] { plan(); });
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
    // Search for a trajectory that begins after one planner period. Computation
    // must finish before this timestamp; publishing a trajectory starting at the
    // old callback time would skip its initial motion on a slow simulated CPU.
    const double executionTime = time + planningBudget_;
    const Vec3 executionPosition =
      position + velocity * planningBudget_ + .5 * gravity * planningBudget_ * planningBudget_;
    const Vec3 executionVelocity = velocity + gravity * planningBudget_;
    // Reserve the remaining fifth of the period for the end of the current
    // bounded IK/candidate check, command publication and controller handoff.
    const auto shouldStop = [&] {
      const auto current = processingClock_.now();
      return !started || !current || *current < *started ||
             static_cast<double>(*current - *started) * 1e-9 >= .8 * planningBudget_;
    };
    const auto result = planner_.search(
      executionTime, desired(executionTime), feedback_.q, executionPosition, executionVelocity,
      *radius_, shouldStop);
    const auto & best = result.segments;
    const auto duration = processingClock_.elapsed(started);
    const auto elapsed = std::get_if<double>(&duration);
    if (best.empty() || !elapsed || *elapsed >= planningBudget_) {
      record_.write(
        {{"time", time},
         {"capture_time", ball_->time},
         {"status", best.empty() && !result.interrupted ? "no_feasible_intercept"
                                                        : "planning_deadline_missed"},
         {"inverse_solutions", static_cast<double>(result.inverseSolutions)},
         {"inverse_calls", static_cast<double>(result.inverseCalls)},
         {"inverse_iterations", static_cast<double>(result.inverseIterations)},
         {"planning_budget_seconds", planningBudget_},
         {"planning_wall_seconds", processingClock_.elapsed(started)}});
      return;
    }
    publish(best);
    // The 60 ms command-update limit applies to published plans. A failed
    // search must leave the next 30 Hz tick free to try a newer observation.
    lastPlan_ = time;
    cupInterceptTime_ = best.back().start;
    record_.write(
      {{"time", time},
       {"capture_time", ball_->time},
       {"status", "planned"},
       {"target", values(result.point)},
       {"target_joints", values(best.front().sample(cupInterceptTime_).q)},
       {"intercept_time", cupInterceptTime_},
       {"trajectory_start_time", executionTime},
       {"inverse_calls", static_cast<double>(result.inverseCalls)},
       {"inverse_iterations", static_cast<double>(result.inverseIterations)},
       {"planning_budget_seconds", planningBudget_},
       {"search_interrupted", result.interrupted ? 1. : 0.},
       {"cup_velocity", values(result.velocity)},
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
    trajectories_.replace(segments, now().seconds());
  }
  MotionState desired(double time) const
  {
    return trajectories_.sample(time, {home_, Vec7::Zero(), Vec7::Zero()});
  }
  Arm arm_;
  CupInterceptPlanner planner_;
  Recorder record_;
  ProcessingClock processingClock_;
  Feedback feedback_;
  Vec7 home_;
  double lastPlan_{-1}, holdUntil_{-1}, planInterval_{.06}, planningBudget_{1. / 30.};
  std::optional<Ball> ball_;
  std::optional<double> radius_;
  TrajectorySchedule trajectories_;
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
