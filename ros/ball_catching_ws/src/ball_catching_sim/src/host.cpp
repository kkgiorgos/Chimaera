// Host-only physics adapter and privileged scorer. No trajectory tracking here.
#include <array>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <thread>

#include <ignition/gazebo/System.hh>
#include <ignition/gazebo/Model.hh>
#include <ignition/gazebo/Link.hh>
#include <ignition/gazebo/components/Joint.hh>
#include <ignition/gazebo/components/JointPosition.hh>
#include <ignition/gazebo/components/JointPositionReset.hh>
#include <ignition/gazebo/components/JointVelocity.hh>
#include <ignition/gazebo/components/JointForceCmd.hh>
#include <ignition/gazebo/components/LinearVelocityCmd.hh>
#include <ignition/gazebo/components/PoseCmd.hh>
#include <ignition/gazebo/components/Model.hh>
#include <ignition/gazebo/components/Name.hh>
#include <ignition/plugin/Register.hh>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_srvs/srv/trigger.hpp>
#include "ball_catching_sim/scoring.hpp"

namespace ball_catching {
namespace gz = ignition::gazebo;
using Vector = ignition::math::Vector3d;

class Host : public gz::System, public gz::ISystemConfigure,
             public gz::ISystemPreUpdate, public gz::ISystemPostUpdate {
 public:
  ~Host() override {
    if (executor_) executor_->cancel();
    if (thread_.joinable()) thread_.join();
    if (context_) context_->shutdown("ball catching host stopped");
  }

  void Configure(const gz::Entity &, const std::shared_ptr<const sdf::Element> &sdf,
                 gz::EntityComponentManager &, gz::EventManager &) override {
    initial_ = sdf->Get<Vector>("launch_position");
    velocity_ = sdf->Get<Vector>("launch_velocity");
    retention_ = sdf->Get<double>("retention");
    radius_ = sdf->Get<double>("cup_radius");
    depth_ = sdf->Get<double>("cup_depth");
    timeout_ = sdf->Get<double>("trial_timeout");
    auto_ = sdf->Get<bool>("auto_throw");
    std::istringstream poses(sdf->Get<std::string>("initial_pose"));
    for (auto &q : initialQ_) poses >> q;
    output_ = sdf->Get<std::string>("output");
    if (!output_.empty()) {
      samples_.open(output_ + "/ground_truth.csv");
      if (!samples_) throw std::runtime_error("Cannot open ground truth output");
      samples_ << "time,ball_x,ball_y,ball_z,cup_x,cup_y,cup_z,cup_qx,cup_qy,cup_qz,cup_qw,inside,launched\n";
    }
    context_ = std::make_shared<rclcpp::Context>();
    context_->init(0, nullptr);
    auto options = rclcpp::NodeOptions().context(context_).use_global_arguments(false);
    node_ = std::make_shared<rclcpp::Node>("ball_catching_host", options);
    state_ = node_->create_publisher<sensor_msgs::msg::JointState>(
        "/robot/joint_states", rclcpp::SensorDataQoS());
    truth_ = node_->create_publisher<geometry_msgs::msg::PoseStamped>(
        "/evaluation/ball_pose", rclcpp::SensorDataQoS());
    result_ = node_->create_publisher<std_msgs::msg::String>(
        "/evaluation/result", rclcpp::QoS(1).reliable().transient_local());
    command_ = node_->create_subscription<sensor_msgs::msg::JointState>(
        "/robot/effort_command", rclcpp::QoS(1).best_effort(),
        [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) {
          if (msg->name.size() != 7 || msg->effort.size() != 7) return;
          std::array<double, 7> candidate;
          for (size_t i = 0; i < 7; ++i) {
            if (msg->name[i] != names_[i] || !std::isfinite(msg->effort[i])) return;
            candidate[i] = std::clamp(msg->effort[i], -limits_[i], limits_[i]);
          }
          std::lock_guard<std::mutex> lock(mutex_);
          torque_ = candidate;
          commandTime_ = rclcpp::Time(msg->header.stamp).seconds();
        });
    readySub_ = node_->create_subscription<std_msgs::msg::Bool>(
        "/robot/ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
          std::lock_guard<std::mutex> lock(mutex_); ready_ = msg->data;
        });
    cameraSub_ = node_->create_subscription<std_msgs::msg::Bool>(
        "/robot/perception_ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
          std::lock_guard<std::mutex> lock(mutex_); cameras_ = msg->data;
        });
    service_ = node_->create_service<std_srvs::srv::Trigger>("/experiment/throw",
        [this](const std::shared_ptr<std_srvs::srv::Trigger::Request>,
               std::shared_ptr<std_srvs::srv::Trigger::Response> reply) {
          std::lock_guard<std::mutex> lock(mutex_);
          reply->success = ready_ && cameras_ && !launched_;
          reply->message = reply->success ? "Throw queued" : "Not ready or already launched";
          if (reply->success) requested_ = true;
        });
    rclcpp::ExecutorOptions executorOptions;
    executorOptions.context = context_;
    executor_ = std::make_shared<rclcpp::executors::SingleThreadedExecutor>(executorOptions);
    executor_->add_node(node_);
    thread_ = std::thread([this] { executor_->spin(); });
  }

  void PreUpdate(const gz::UpdateInfo &info, gz::EntityComponentManager &ecm) override {
    if (info.paused) return;
    const double now = std::chrono::duration<double>(info.simTime).count();
    if (!initialized_) {
      auto armEntity = ecm.EntityByComponents(gz::components::Model(), gz::components::Name("fr3"));
      auto ballEntity = ecm.EntityByComponents(gz::components::Model(), gz::components::Name("tennis_ball"));
      if (!armEntity || !ballEntity) return;
      gz::Model arm(armEntity);
      ballModel_ = gz::Model(ballEntity);
      ball_ = gz::Link(ballModel_.LinkByName(ecm, "ball"));
      cup_ = gz::Link(arm.LinkByName(ecm, "catch_cup"));
      if (!cup_.Valid(ecm)) throw std::runtime_error("Named cup link not preserved in SDF");
      ball_.EnableVelocityChecks(ecm);
      cup_.EnableVelocityChecks(ecm);
      for (size_t i = 0; i < 7; ++i) {
        joints_[i] = arm.JointByName(ecm, names_[i]);
        if (!joints_[i]) throw std::runtime_error("Missing FR3 joint");
        ecm.CreateComponent(joints_[i], gz::components::JointPosition());
        ecm.CreateComponent(joints_[i], gz::components::JointVelocity());
        ecm.CreateComponent(joints_[i], gz::components::JointPositionReset({initialQ_[i]}));
      }
      initialized_ = true;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    // Fortress clears velocity commands to zero but leaves the component:
    // remove it after the launch step so subsequent motion is free ballistic.
    if (launched_ && now > launchTime_) {
      ecm.RemoveComponent<gz::components::LinearVelocityCmd>(ball_.Entity());
      ecm.RemoveComponent<gz::components::WorldPoseCmd>(ballModel_.Entity());
    }
    for (size_t i = 0; i < 7; ++i) {
      const double torque = now - commandTime_ <= 0.1 && commandTime_ <= now + 0.002 ? torque_[i] : 0;
      ecm.SetComponentData<gz::components::JointForceCmd>(joints_[i], {torque});
    }
    if (!launched_ && (requested_ || (auto_ && ready_ && cameras_))) {
      ballModel_.SetWorldPoseCmd(ecm, ignition::math::Pose3d(initial_, ignition::math::Quaterniond::Identity));
      ball_.SetLinearVelocity(ecm, velocity_);
      launched_ = true;
      launchTime_ = now;
      RCLCPP_INFO(node_->get_logger(), "Ball launched at simulation time %.3f", now);
    }
  }

  void PostUpdate(const gz::UpdateInfo &info, const gz::EntityComponentManager &ecm) override {
    if (!initialized_ || info.paused) return;
    const double now = std::chrono::duration<double>(info.simTime).count();
    if (now - lastPublish_ < 0.004 - 1e-8) return;
    lastPublish_ = now;
    builtin_interfaces::msg::Time stamp = rclcpp::Time(static_cast<int64_t>(now * 1e9));
    sensor_msgs::msg::JointState state;
    state.header.stamp = stamp;
    for (size_t i = 0; i < 7; ++i) {
      const auto p = ecm.Component<gz::components::JointPosition>(joints_[i]);
      const auto v = ecm.Component<gz::components::JointVelocity>(joints_[i]);
      if (!p || !v || p->Data().empty() || v->Data().empty()) return;
      state.name.push_back(names_[i]);
      state.position.push_back(p->Data()[0]);
      state.velocity.push_back(v->Data()[0]);
    }
    state_->publish(state);
    auto bp = ball_.WorldPose(ecm);
    auto cp = cup_.WorldPose(ecm);
    if (!bp || !cp) return;
    geometry_msgs::msg::PoseStamped truth;
    truth.header.stamp = stamp;
    truth.header.frame_id = "world";
    truth.pose.position.x = bp->Pos().X();
    truth.pose.position.y = bp->Pos().Y();
    truth.pose.position.z = bp->Pos().Z();
    truth.pose.orientation.w = 1;
    truth_->publish(truth);
    const Vector relative = cp->Rot().Inverse().RotateVector(bp->Pos() - cp->Pos());
    constexpr double ballRadius = 0.0335;
    // Match the actual 20-sided cavity, not an inscribed circle that wrongly
    // reports a ball resting against a polygon wall as repeatedly leaving.
    // A 2 mm tolerance accounts for contact solver penetration, far below the
    // ball radius. Vertical escape still resets the retention interval.
    const bool inside = contained(relative.X(), relative.Y(), relative.Z(), radius_, depth_, ballRadius);
    bool launched;
    { std::lock_guard<std::mutex> lock(mutex_); launched = launched_; }
    if (samples_) samples_ << std::setprecision(10) << now << ',' << bp->Pos().X() << ',' << bp->Pos().Y()
        << ',' << bp->Pos().Z() << ',' << cp->Pos().X() << ',' << cp->Pos().Y() << ',' << cp->Pos().Z()
        << ',' << cp->Rot().X() << ',' << cp->Rot().Y() << ',' << cp->Rot().Z() << ',' << cp->Rot().W()
        << ',' << inside << ',' << launched << '\n';
    if (!launched || finished_) return;
    if (retained_.Update(inside, now, retention_)) Finish(true, "retained", now);
    else if (now - launchTime_ >= timeout_) Finish(false, "not_retained", now);
  }

 private:
  void Finish(bool success, const std::string &reason, double now) {
    finished_ = true;
    std::ostringstream json;
    json << std::boolalpha << "{\"success\":" << success << ",\"reason\":\"" << reason
         << "\",\"launch_time\":" << launchTime_ << ",\"result_time\":" << now
         << ",\"retention\":" << retention_ << "}";
    std_msgs::msg::String message;
    message.data = json.str();
    result_->publish(message);
    if (samples_) samples_.flush();
    if (!output_.empty()) { std::ofstream file(output_ + "/result.json"); file << message.data << '\n'; }
    RCLCPP_INFO(node_->get_logger(), "Catch result: %s", message.data.c_str());
  }
  std::array<std::string, 7> names_{"fr3_joint1", "fr3_joint2", "fr3_joint3", "fr3_joint4",
                                    "fr3_joint5", "fr3_joint6", "fr3_joint7"};
  std::array<double, 7> limits_{87, 87, 87, 87, 12, 12, 12};
  std::array<double, 7> initialQ_{}, torque_{};
  std::array<gz::Entity, 7> joints_{};
  gz::Model ballModel_;
  gz::Link ball_, cup_;
  Vector initial_, velocity_;
  double retention_{1}, radius_{0.12}, depth_{0.14}, timeout_{4};
  double commandTime_{-1}, launchTime_{0}, lastPublish_{-1};
  Retention retained_;
  bool initialized_{false}, auto_{true}, ready_{false}, cameras_{false}, requested_{false};
  bool launched_{false}, finished_{false};
  std::mutex mutex_;
  std::string output_;
  std::ofstream samples_;
  rclcpp::Context::SharedPtr context_;
  rclcpp::Node::SharedPtr node_;
  rclcpp::executors::SingleThreadedExecutor::SharedPtr executor_;
  std::thread thread_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr state_;
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr truth_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr result_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr command_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr readySub_, cameraSub_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
};
}
IGNITION_ADD_PLUGIN(ball_catching::Host, ignition::gazebo::System,
                    ball_catching::Host::ISystemConfigure, ball_catching::Host::ISystemPreUpdate,
                    ball_catching::Host::ISystemPostUpdate)
