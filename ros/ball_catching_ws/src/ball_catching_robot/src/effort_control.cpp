#include "ball_catching_robot/runtime.hpp"
#include <std_msgs/msg/bool.hpp>
#include <trajectory_msgs/msg/joint_trajectory.hpp>

namespace ball_catching {
class EffortControl : public rclcpp::Node {
 public:
  EffortControl() : Node("ball_effort_control"), arm_(readFile(declare_parameter<std::string>("robot_file", ""))),
      home_(jointVector(declare_parameter<std::vector<double>>("initial_pose",
        {0., -.7853981633974483, 0., -2.356194490192345, 0., 1.5707963267948966, .7853981633974483}))),
      record_(declare_parameter<std::string>("output", ""), "control") {
    arm_.validatePose(home_);
    const double hz = declare_parameter("control_hz", 250.);
    if (!std::isfinite(hz) || hz <= 0) throw std::invalid_argument("control_hz must be positive and finite");
    commands_ = create_publisher<sensor_msgs::msg::JointState>("/robot/effort_command", rclcpp::SensorDataQoS());
    ready_ = create_publisher<std_msgs::msg::Bool>("/robot/ready", 1);
    joints_ = create_subscription<sensor_msgs::msg::JointState>("/robot/joint_states", rclcpp::SensorDataQoS(),
        [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) { feedback_.update(*msg, arm_.names); });
    targets_ = create_subscription<trajectory_msgs::msg::JointTrajectory>("/robot/joint_trajectory", 1,
        [this](trajectory_msgs::msg::JointTrajectory::ConstSharedPtr msg) { target(*msg); });
    timer_ = timer(*this, 1./hz, [this] { control(); });
  }
 private:
  void target(const trajectory_msgs::msg::JointTrajectory &msg) {
    if (msg.joint_names != arm_.names || msg.points.size() != 2) return;
    const auto &first = msg.points[0]; const auto &last = msg.points[1];
    try {
      Quintic candidate(seconds(msg.header.stamp), seconds(last.time_from_start),
          {jointVector(first.positions), jointVector(first.velocities), jointVector(first.accelerations)},
          jointVector(last.positions));
      if (candidate.feasible(arm_)) trajectory_ = candidate;
    } catch (const std::invalid_argument &) { return; }
  }
  void control() {
    const auto stamp = now();
    const double time = stamp.seconds();
    if (!feedback_.valid || time - feedback_.time < 0 || time - feedback_.time >= .05) return;
    const MotionState desired = trajectory_ ? trajectory_->sample(time) : MotionState{home_, Vec7::Zero(), Vec7::Zero()};
    const Vec7 correction = desired.ddq + 144*(desired.q - feedback_.q) + 24*(desired.dq - feedback_.dq);
    Vec7 effort = arm_.forces(feedback_.q, feedback_.dq) + arm_.mass(feedback_.q)*correction;
    for (int i = 0; i < 7; ++i) {
      const double speed = feedback_.dq[i];
      effort[i] -= 20*((speed > 0) - (speed < 0))*std::max(std::abs(speed) - arm_.velocity[i], 0.);
      effort[i] = std::clamp(effort[i], -arm_.effort[i], arm_.effort[i]);
    }
    sensor_msgs::msg::JointState msg;
    msg.header.stamp = stamp; msg.name = arm_.names; msg.effort = values(effort); commands_->publish(msg);
    if (time - lastLog_ > .04) {
      lastLog_ = time;
      std_msgs::msg::Bool ready;
      ready.data = !trajectory_ && (feedback_.q - home_).cwiseAbs().maxCoeff() < .025 && feedback_.dq.cwiseAbs().maxCoeff() < .10;
      ready_->publish(ready);
      record_.write({{"time", time}, {"feedback_time", feedback_.time}, {"joints", values(feedback_.q)},
                     {"velocity", values(feedback_.dq)}, {"desired", values(desired.q)}, {"effort", values(effort)}});
    }
  }
  Arm arm_;
  Vec7 home_;
  Recorder record_;
  Feedback feedback_;
  double lastLog_{-1};
  std::optional<Quintic> trajectory_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr commands_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr ready_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<trajectory_msgs::msg::JointTrajectory>::SharedPtr targets_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}
int main(int argc, char **argv) { return ball_catching::run<ball_catching::EffortControl>(argc, argv); }
