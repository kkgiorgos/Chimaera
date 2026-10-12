// Host-only physics adapter and privileged scorer. No trajectory tracking here.
#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <ignition/gazebo/EventManager.hh>
#include <ignition/gazebo/Events.hh>
#include <ignition/gazebo/Link.hh>
#include <ignition/gazebo/Model.hh>
#include <ignition/gazebo/System.hh>
#include <ignition/gazebo/components/Joint.hh>
#include <ignition/gazebo/components/JointForceCmd.hh>
#include <ignition/gazebo/components/JointPosition.hh>
#include <ignition/gazebo/components/JointPositionReset.hh>
#include <ignition/gazebo/components/JointVelocity.hh>
#include <ignition/gazebo/components/LinearVelocityCmd.hh>
#include <ignition/gazebo/components/Model.hh>
#include <ignition/gazebo/components/Name.hh>
#include <ignition/gazebo/components/PoseCmd.hh>
#include <ignition/plugin/Register.hh>
#include <iomanip>
#include <mutex>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <sstream>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <thread>

#include "ball_catching_sim/scoring.hpp"

namespace ball_catching
{
namespace gz = ignition::gazebo;
using Vector = ignition::math::Vector3d;

class Host : public gz::System,
             public gz::ISystemConfigure,
             public gz::ISystemPreUpdate,
             public gz::ISystemPostUpdate
{
public:
  ~Host() override
  {
    if (executor_) executor_->cancel();
    if (thread_.joinable()) thread_.join();
    if (context_) context_->shutdown("ball catching host stopped");
  }

  void Configure(
    const gz::Entity &, const std::shared_ptr<const sdf::Element> & sdf,
    gz::EntityComponentManager &, gz::EventManager & events) override
  {
    events_ = &events;
    initial_ = sdf->Get<Vector>("launch_position");
    velocity_ = sdf->Get<Vector>("launch_velocity");
    radius_ = sdf->Get<double>("cup_radius");
    depth_ = sdf->Get<double>("cup_depth");
    duration_ = sdf->Get<double>("duration");
    score_.emplace(duration_);
    auto_ = sdf->Get<bool>("auto_throw");
    feedbackPeriod_ = 1. / (sdf->HasElement("joint_hz") ? sdf->Get<double>("joint_hz") : 250.);
    warmup_ = sdf->HasElement("warmup") ? sdf->Get<double>("warmup") : 0.;
    std::istringstream poses(sdf->Get<std::string>("initial_pose"));
    for (auto & q : initialQ_) poses >> q;
    output_ = sdf->Get<std::string>("output");
    if (!output_.empty()) {
      samples_.open(output_ + "/ground_truth.csv");
      if (!samples_) throw std::runtime_error("Cannot open ground truth output");
      samples_ << "time,ball_x,ball_y,ball_z,cup_x,cup_y,cup_z,cup_qx,cup_qy,cup_qz,cup_qw,inside,"
                  "launched,ball_vx,ball_vy,ball_vz";
      for (size_t i = 0; i < names_.size(); ++i) samples_ << ',' << names_[i];
      samples_ << '\n';
      commands_.open(output_ + "/commands.csv");
      if (!commands_) throw std::runtime_error("Cannot open command receipt output");
      commands_ << "receipt_time,command_time\n" << std::setprecision(17);
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
        std::copy(candidate.begin(), candidate.end(), torque_.begin());
        commandTime_ = rclcpp::Time(msg->header.stamp).seconds();
        if (commands_) {
          commands_ << worldTime_ << ',' << commandTime_ << '\n';
          commands_.flush();
        }
      });
    readySub_ = node_->create_subscription<std_msgs::msg::Bool>(
      "/robot/ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
        std::lock_guard<std::mutex> lock(mutex_);
        ready_ = msg->data;
      });
    cameraSub_ = node_->create_subscription<std_msgs::msg::Bool>(
      "/robot/perception_ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
        std::lock_guard<std::mutex> lock(mutex_);
        cameras_ = msg->data;
      });
    service_ = node_->create_service<std_srvs::srv::Trigger>(
      "/experiment/throw", [this](
                             const std::shared_ptr<std_srvs::srv::Trigger::Request>,
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

  void PreUpdate(const gz::UpdateInfo & info, gz::EntityComponentManager & ecm) override
  {
    if (info.paused) return;
    const double now = std::chrono::duration<double>(info.simTime).count();
    if (!initialized_) {
      auto armEntity = ecm.EntityByComponents(gz::components::Model(), gz::components::Name("fr3"));
      auto ballEntity =
        ecm.EntityByComponents(gz::components::Model(), gz::components::Name("tennis_ball"));
      if (!armEntity || !ballEntity) return;
      gz::Model arm(armEntity);
      ballModel_ = gz::Model(ballEntity);
      ball_ = gz::Link(ballModel_.LinkByName(ecm, "ball"));
      cup_ = gz::Link(arm.LinkByName(ecm, "catch_cup"));
      if (!cup_.Valid(ecm)) throw std::runtime_error("Named tool link not preserved in SDF");
      ball_.EnableVelocityChecks(ecm);
      cup_.EnableVelocityChecks(ecm);
      for (size_t i = 0; i < names_.size(); ++i) {
        joints_[i] = arm.JointByName(ecm, names_[i]);
        if (!joints_[i]) throw std::runtime_error("Missing FR3 joint");
        ecm.CreateComponent(joints_[i], gz::components::JointPosition());
        ecm.CreateComponent(joints_[i], gz::components::JointVelocity());
        ecm.CreateComponent(joints_[i], gz::components::JointPositionReset({initialQ_[i]}));
      }
      initialized_ = true;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    worldTime_ = now;
    // Fortress clears velocity commands to zero but leaves the component:
    // remove it after the launch step so subsequent motion is free ballistic.
    if (launched_ && now > launchTime_) {
      ecm.RemoveComponent<gz::components::LinearVelocityCmd>(ball_.Entity());
      ecm.RemoveComponent<gz::components::WorldPoseCmd>(ballModel_.Entity());
    }
    for (size_t i = 0; i < names_.size(); ++i) {
      const double stamp = commandTime_;
      const double torque = now - stamp <= 0.1 && stamp <= now + 0.002 ? torque_[i] : 0;
      ecm.SetComponentData<gz::components::JointForceCmd>(joints_[i], {torque});
    }
    if (ready_ && cameras_) {
      if (readySince_ < 0) readySince_ = now;
    } else {
      readySince_ = -1;
    }
    if (
      !launched_ && readySince_ >= 0 && now - readySince_ + 1e-8 >= warmup_ &&
      (requested_ || auto_)) {
      ballModel_.SetWorldPoseCmd(
        ecm, ignition::math::Pose3d(initial_, ignition::math::Quaterniond::Identity));
      ball_.SetLinearVelocity(ecm, velocity_);
      launched_ = true;
      launchTime_ = now;
      if (!output_.empty()) {
        std::ofstream launch(output_ + "/launch.json");
        launch << std::setprecision(17) << "{\"launch_time\":" << now << "}\n";
      }
      RCLCPP_INFO(node_->get_logger(), "Ball launched at simulation time %.3f", now);
    }
  }

  void PostUpdate(const gz::UpdateInfo & info, const gz::EntityComponentManager & ecm) override
  {
    if (!initialized_ || info.paused || finished_) return;
    const double now = std::chrono::duration<double>(info.simTime).count();
    const auto bp = ball_.WorldPose(ecm);
    const auto cp = cup_.WorldPose(ecm);
    if (!bp || !cp) return;
    const Vector relative = cp->Rot().Inverse().RotateVector(bp->Pos() - cp->Pos());
    // Match the physical 20-sided cavity, with 2 mm contact-solver tolerance.
    const bool inside = contained(relative.X(), relative.Y(), relative.Z(), radius_, depth_, .0335);
    bool launched;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      launched = launched_;
    }
    // Track containment at every physics step. Evaluate on the first step at
    // the deadline, even when it falls between normal 250 Hz feedback samples.
    const auto evaluation = launched ? score_->Update(inside, now, launchTime_) : std::nullopt;
    if (!evaluation && now - lastPublish_ < feedbackPeriod_ - 1e-8) return;
    lastPublish_ = now;
    builtin_interfaces::msg::Time stamp = rclcpp::Time(static_cast<int64_t>(now * 1e9));
    sensor_msgs::msg::JointState state;
    state.header.stamp = stamp;
    for (size_t i = 0; i < names_.size(); ++i) {
      const auto p = ecm.Component<gz::components::JointPosition>(joints_[i]);
      const auto v = ecm.Component<gz::components::JointVelocity>(joints_[i]);
      if (!p || !v || p->Data().empty() || v->Data().empty()) return;
      state.name.push_back(names_[i]);
      state.position.push_back(p->Data()[0]);
      state.velocity.push_back(v->Data()[0]);
    }
    state_->publish(state);
    geometry_msgs::msg::PoseStamped truth;
    truth.header.stamp = stamp;
    truth.header.frame_id = "world";
    truth.pose.position.x = bp->Pos().X();
    truth.pose.position.y = bp->Pos().Y();
    truth.pose.position.z = bp->Pos().Z();
    truth.pose.orientation.w = 1;
    truth_->publish(truth);
    const Vector bv = ball_.WorldLinearVelocity(ecm).value_or(Vector::Zero);
    if (samples_) {
      samples_ << std::setprecision(10) << now << ',' << bp->Pos().X() << ',' << bp->Pos().Y()
               << ',' << bp->Pos().Z() << ',' << cp->Pos().X() << ',' << cp->Pos().Y() << ','
               << cp->Pos().Z() << ',' << cp->Rot().X() << ',' << cp->Rot().Y() << ','
               << cp->Rot().Z() << ',' << cp->Rot().W() << ',' << inside << ',' << launched << ','
               << bv.X() << ',' << bv.Y() << ',' << bv.Z();
      for (double q : state.position) samples_ << ',' << q;
      samples_ << '\n';
    }
    if (evaluation) Finish(*evaluation, now);
  }

private:
  void Finish(const Evaluation & evaluation, double now)
  {
    finished_ = true;
    std::ostringstream json;
    json << std::boolalpha << std::setprecision(10) << "{\"success\":" << evaluation.success
         << ",\"reason\":\""
         << (evaluation.success ? "inside_cup_at_deadline" : "outside_cup_at_deadline")
         << "\",\"launch_time\":" << launchTime_ << ",\"result_time\":" << now
         << ",\"duration\":" << duration_
         << ",\"contained_duration\":" << evaluation.containedDuration
         << ",\"flight_end_time\":" << evaluation.captureTime.value_or(now)
         << ",\"flight_end_kind\":\"" << (evaluation.success ? "cup_capture" : "duration_elapsed")
         << "\"}";
    std_msgs::msg::String message;
    message.data = json.str();
    result_->publish(message);
    if (samples_) samples_.flush();
    if (!output_.empty()) {
      std::ofstream file(output_ + "/result.json");
      file << message.data << '\n';
    }
    events_->Emit<gz::events::Pause>(true);
    RCLCPP_INFO(node_->get_logger(), "Catch result: %s", message.data.c_str());
  }
  std::array<std::string, 7> names_{"fr3_joint1", "fr3_joint2", "fr3_joint3", "fr3_joint4",
                                    "fr3_joint5", "fr3_joint6", "fr3_joint7"};
  std::array<double, 7> limits_{87, 87, 87, 87, 12, 12, 12};
  std::array<double, 7> initialQ_{}, torque_{};
  std::array<gz::Entity, 7> joints_{};
  gz::EventManager * events_{};
  gz::Model ballModel_;
  gz::Link ball_, cup_;
  Vector initial_, velocity_;
  double duration_{2}, radius_{0.12}, depth_{0.14};
  double commandTime_{-1}, launchTime_{0}, lastPublish_{-1};
  double feedbackPeriod_{.004}, warmup_{0}, readySince_{-1}, worldTime_{0};
  std::ofstream commands_;
  std::optional<DeadlineScore> score_;
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
}  // namespace ball_catching
IGNITION_ADD_PLUGIN(
  ball_catching::Host, ignition::gazebo::System, ball_catching::Host::ISystemConfigure,
  ball_catching::Host::ISystemPreUpdate, ball_catching::Host::ISystemPostUpdate)
