// Stock-hand motor feedback control. No simulator contacts or ball truth.
#include <array>
#include <geometry_msgs/msg/vector3_stamped.hpp>
#include <std_msgs/msg/bool.hpp>

#include "ball_catching_robot/runtime.hpp"

namespace ball_catching
{
class GripperControl : public rclcpp::Node
{
public:
  GripperControl()
  : Node("ball_gripper_control"), record_(declare_parameter<std::string>("output", ""), "gripper")
  {
    force_ = declare_parameter("grasp_force", 50.);
    if (!std::isfinite(force_) || force_ < 30 || force_ > 70)
      throw std::invalid_argument("Grasp force must be 30–70 N");
    commands_ = create_publisher<sensor_msgs::msg::JointState>(
      "/robot/gripper_effort", rclcpp::SensorDataQoS());
    status_ = create_publisher<geometry_msgs::msg::Vector3Stamped>(
      "/robot/gripper_state", rclcpp::SensorDataQoS());
    ready_ = create_publisher<std_msgs::msg::Bool>("/robot/gripper_ready", 1);
    joints_ = create_subscription<sensor_msgs::msg::JointState>(
      "/robot/joint_states", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) {
        for (int i = 0; i < 2; ++i) {
          auto found = std::find(msg->name.begin(), msg->name.end(), names_[i]);
          if (found == msg->name.end()) return;
          const auto j = static_cast<size_t>(found - msg->name.begin());
          if (
            j >= msg->position.size() || j >= msg->velocity.size() ||
            !std::isfinite(msg->position[j]) || !std::isfinite(msg->velocity[j]))
            return;
          position_[i] = msg->position[j];
          velocity_[i] = msg->velocity[j];
        }
        feedbackTime_ = seconds(msg->header.stamp);
      });
    target_ = create_subscription<geometry_msgs::msg::Vector3Stamped>(
      "/robot/gripper_target", 1, [this](geometry_msgs::msg::Vector3Stamped::ConstSharedPtr msg) {
        if (
          !std::isfinite(msg->vector.x) || msg->vector.x < 0 || msg->vector.x > .08 ||
          !std::isfinite(msg->vector.y) || msg->vector.y < 0 || msg->vector.y > .08)
          return;
        targetWidth_ = msg->vector.x;
        objectWidth_ = msg->vector.y;
        // Once closure has started, a refinement of the visual interception
        // must not briefly reopen the fingers because its timestamp is later.
        const double requestedClose = seconds(msg->header.stamp);
        closeAt_ =
          now().seconds() >= closeAt_ ? std::min(closeAt_, requestedClose) : requestedClose;
        graspedSince_ = -1;
      });
    timer_ = timer(*this, .004, [this] { control(); });
  }

private:
  void control()
  {
    const double time = now().seconds();
    if (feedbackTime_ < 0 || time < feedbackTime_ || time - feedbackTime_ > .05) return;
    const auto started = WallClock::now();
    const bool closing = time >= closeAt_ && targetWidth_ < .08;
    const double goal = closing ? targetWidth_ / 2 : .04;
    const double width = position_[0] + position_[1];
    sensor_msgs::msg::JointState command;
    command.header.stamp = now();
    command.name = names_;
    for (int i = 0; i < 2; ++i) {
      const double error = goal - position_[i];
      const double speed = std::clamp(30 * error, -.05, .05);
      // Virtual motor servo belongs to robot compute; host only bounds/applies
      // forces. Conservatively split the commanded total force between fingers.
      const bool gripRegion = closing && width <= objectWidth_ + .001;
      command.effort.push_back(
        gripRegion ? -force_ / 2 : std::clamp(8 * (speed - velocity_[i]), -force_ / 2, force_ / 2));
    }
    // The commercial hand observes its coupled actuator width. Independent
    // simulated fingers can drift oppositely under contact while width is held.
    const bool stalled = std::abs(velocity_[0] + velocity_[1]) < .003;
    const bool inWidth =
      objectWidth_ > .01 && std::abs(width - objectWidth_) < .006 && width > targetWidth_ + .001;
    // Confirm the initial motor stall for 20 ms, then retain grasp status while
    // width stays within the object's tolerance. Loaded wrist motion can cause
    // brief width velocity changes without losing a held object.
    if (!closing || !inWidth)
      graspedSince_ = -1;
    else if (graspedSince_ < 0 && stalled)
      graspedSince_ = time;
    else if (graspedSince_ >= 0 && time - graspedSince_ < .02 && !stalled)
      graspedSince_ = -1;
    geometry_msgs::msg::Vector3Stamped status;
    status.header.stamp = command.header.stamp;
    status.vector.x = width;
    status.vector.y = graspedSince_ >= 0 && time - graspedSince_ >= .02 ? 1 : 0;
    status_->publish(status);
    commands_->publish(command);
    std_msgs::msg::Bool ready;
    ready.data = !closing && width > .078 && stalled;
    ready_->publish(ready);
    record_.write(
      {{"time", time},
       {"processing_wall_seconds", elapsed(started)},
       {"width", width},
       {"target_width", goal * 2},
       {"grasped", status.vector.y}});
  }
  Recorder record_;
  const std::vector<std::string> names_{"fr3_finger_joint1", "fr3_finger_joint2"};
  std::array<double, 2> position_{}, velocity_{};
  double force_, feedbackTime_{-1}, targetWidth_{.08}, objectWidth_{0};
  double closeAt_{1e30}, graspedSince_{-1};
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr commands_;
  rclcpp::Publisher<geometry_msgs::msg::Vector3Stamped>::SharedPtr status_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr ready_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<geometry_msgs::msg::Vector3Stamped>::SharedPtr target_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}  // namespace ball_catching
int main(int argc, char ** argv)
{
  return ball_catching::run<ball_catching::GripperControl>(argc, argv);
}
