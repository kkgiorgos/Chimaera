#include <std_msgs/msg/bool.hpp>
#include <trajectory_msgs/msg/joint_trajectory.hpp>

#include "ball_catching_robot/runtime.hpp"

namespace ball_catching
{
class EffortControl : public rclcpp::Node
{
public:
  EffortControl()
  : Node("ball_effort_control"),
    arm_(readFile(declare_parameter<std::string>("robot_file", ""))),
    home_(jointVector(declare_parameter<std::vector<double>>(
      "initial_pose", {0., -.7853981633974483, 0., -2.356194490192345, 0., 1.5707963267948966,
                       .7853981633974483}))),
    record_(declare_parameter<std::string>("output", ""), "control"),
    timing_(get_parameter("output").as_string(), "control_timing")
  {
    arm_.validatePose(home_);
    const double hz = declare_parameter("control_hz", 250.);
    if (!std::isfinite(hz) || hz <= 0)
      throw std::invalid_argument("control_hz must be positive and finite");
    commands_ = create_publisher<sensor_msgs::msg::JointState>(
      "/robot/effort_command", rclcpp::SensorDataQoS());
    ready_ = create_publisher<std_msgs::msg::Bool>("/robot/ready", 1);
    joints_ = create_subscription<sensor_msgs::msg::JointState>(
      "/robot/joint_states", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) {
        feedback_.update(*msg, arm_.names);
      });
    targets_ = create_subscription<trajectory_msgs::msg::JointTrajectory>(
      "/robot/joint_trajectory", 1,
      [this](trajectory_msgs::msg::JointTrajectory::ConstSharedPtr msg) { target(*msg); });
    timer_ = timer(*this, 1. / hz, [this] { control(); });
  }

private:
  void target(const trajectory_msgs::msg::JointTrajectory & msg)
  {
    if (msg.joint_names != arm_.names || msg.points.size() < 2 || msg.points.size() > 4) return;
    try {
      std::vector<Quintic> candidates;
      for (size_t i = 1; i < msg.points.size(); ++i) {
        const auto & first = msg.points[i - 1];
        const auto & last = msg.points[i];
        const double offset = seconds(first.time_from_start);
        Quintic candidate(
          seconds(msg.header.stamp) + offset, seconds(last.time_from_start) - offset,
          {jointVector(first.positions), jointVector(first.velocities),
           jointVector(first.accelerations)},
          MotionState{
            jointVector(last.positions), jointVector(last.velocities),
            jointVector(last.accelerations)});
        if (!candidate.feasible(arm_)) return;
        candidates.push_back(candidate);
      }
      trajectories_ = candidates;
    } catch (const std::invalid_argument &) {
      return;
    }
  }
  void control()
  {
    const auto stamp = now();
    const double time = stamp.seconds();
    if (!feedback_.valid || time - feedback_.time < 0 || time - feedback_.time >= .05) return;
    const auto started = WallClock::now();
    MotionState desired{home_, Vec7::Zero(), Vec7::Zero()};
    for (const auto & trajectory : trajectories_) {
      desired = trajectory.sample(time);
      if (time <= trajectory.start + trajectory.duration) break;
    }
    const Vec7 correction =
      desired.ddq + 144 * (desired.q - feedback_.q) + 24 * (desired.dq - feedback_.dq);
    Vec7 effort = arm_.forces(feedback_.q, feedback_.dq) + arm_.mass(feedback_.q) * correction;
    const double dt = lastControl_ < 0 ? 0. : std::clamp(time - lastControl_, 0., .02);
    lastControl_ = time;
    for (int i = 0; i < 7; ++i) {
      // Bounded torque integral removes static-friction tracking offsets,
      // particularly for the low-inertia final wrist joint. Freeze integration
      // when its correction would push a saturated actuator further outward.
      const double error = desired.q[i] - feedback_.q[i];
      if (std::abs(effort[i] + integral_[i]) < arm_.effort[i] || error * effort[i] < 0.)
        integral_[i] = std::clamp(integral_[i] + 4. * dt * error, -1., 1.);
      effort[i] += integral_[i];
      const double speed = feedback_.dq[i];
      effort[i] -=
        20 * ((speed > 0) - (speed < 0)) * std::max(std::abs(speed) - arm_.velocity[i], 0.);
      effort[i] = std::clamp(effort[i], -arm_.effort[i], arm_.effort[i]);
    }
    sensor_msgs::msg::JointState msg;
    msg.header.stamp = stamp;
    msg.name = arm_.names;
    msg.effort = values(effort);
    commands_->publish(msg);
    timing_.write({{"time", time}, {"processing_wall_seconds", elapsed(started)}});
    if (time - lastLog_ > .04) {
      lastLog_ = time;
      std_msgs::msg::Bool ready;
      ready.data = trajectories_.empty() && (feedback_.q - home_).cwiseAbs().maxCoeff() < .025 &&
                   feedback_.dq.cwiseAbs().maxCoeff() < .10;
      ready_->publish(ready);
      record_.write(
        {{"time", time},
         {"feedback_time", feedback_.time},
         {"joints", values(feedback_.q)},
         {"velocity", values(feedback_.dq)},
         {"desired", values(desired.q)},
         {"integral_torque", values(integral_)},
         {"effort", values(effort)},
         {"processing_wall_seconds", elapsed(started)}});
    }
  }
  Arm arm_;
  Vec7 home_;
  Recorder record_, timing_;
  Feedback feedback_;
  double lastLog_{-1}, lastControl_{-1};
  Vec7 integral_ = Vec7::Zero();
  std::vector<Quintic> trajectories_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr commands_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr ready_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<trajectory_msgs::msg::JointTrajectory>::SharedPtr targets_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}  // namespace ball_catching
int main(int argc, char ** argv)
{
  return ball_catching::run<ball_catching::EffortControl>(argc, argv);
}
