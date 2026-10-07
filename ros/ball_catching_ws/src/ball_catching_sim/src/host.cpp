// Host-only physics adapter and privileged scorer. No trajectory tracking here.
#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <ignition/gazebo/Link.hh>
#include <ignition/gazebo/Model.hh>
#include <ignition/gazebo/System.hh>
#include <ignition/gazebo/components/Collision.hh>
#include <ignition/gazebo/components/ContactSensor.hh>
#include <ignition/gazebo/components/ContactSensorData.hh>
#include <ignition/gazebo/components/Joint.hh>
#include <ignition/gazebo/components/JointForceCmd.hh>
#include <ignition/gazebo/components/JointPosition.hh>
#include <ignition/gazebo/components/JointPositionReset.hh>
#include <ignition/gazebo/components/JointVelocity.hh>
#include <ignition/gazebo/components/JointVelocityLimitsCmd.hh>
#include <ignition/gazebo/components/LinearVelocityCmd.hh>
#include <ignition/gazebo/components/Model.hh>
#include <ignition/gazebo/components/Name.hh>
#include <ignition/gazebo/components/ParentEntity.hh>
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
    gz::EntityComponentManager &, gz::EventManager &) override
  {
    initial_ = sdf->Get<Vector>("launch_position");
    velocity_ = sdf->Get<Vector>("launch_velocity");
    retention_ = sdf->Get<double>("retention");
    radius_ = sdf->Get<double>("cup_radius");
    depth_ = sdf->Get<double>("cup_depth");
    timeout_ = sdf->Get<double>("trial_timeout");
    auto_ = sdf->Get<bool>("auto_throw");
    std::istringstream poses(sdf->Get<std::string>("initial_pose"));
    for (auto & q : initialQ_) poses >> q;
    output_ = sdf->Get<std::string>("output");
    gripper_ = sdf->Get<std::string>("mode") == "gripper";
    drag_ = sdf->Get<double>("drag_factor");
    present_ = sdf->Get<bool>("present");
    allowedBounces_ = sdf->Get<int>("allowed_bounces", 0).first;
    courtRestitution_ = sdf->Get<double>("court_restitution", .745).first;
    courtTangentRatio_ = sdf->Get<double>("court_tangent_ratio", .6).first;
    if (
      allowedBounces_ < 0 || allowedBounces_ > 4 || !std::isfinite(courtRestitution_) ||
      courtRestitution_ <= 0 || courtRestitution_ > 1 || !std::isfinite(courtTangentRatio_) ||
      courtTangentRatio_ <= 0 || courtTangentRatio_ > 1)
      throw std::invalid_argument("Invalid court bounce configuration");
    if (!output_.empty()) {
      samples_.open(output_ + "/ground_truth.csv");
      if (!samples_) throw std::runtime_error("Cannot open ground truth output");
      samples_ << "time,ball_x,ball_y,ball_z,cup_x,cup_y,cup_z,cup_qx,cup_qy,cup_qz,cup_qw,inside,"
                  "launched,ball_vx,ball_vy,ball_vz,left_contact,right_contact,bounce_count";
      for (size_t i = 0; i < (gripper_ ? 9u : 7u); ++i) samples_ << ',' << names_[i];
      samples_ << '\n';
      bounces_.open(output_ + "/bounces.jsonl");
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
      });
    if (gripper_) {
      gripCommand_ = node_->create_subscription<sensor_msgs::msg::JointState>(
        "/robot/gripper_effort", rclcpp::QoS(1).best_effort(),
        [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) {
          if (msg->name.size() != 2 || msg->effort.size() != 2) return;
          for (int i = 0; i < 2; ++i)
            if (msg->name[i] != names_[7 + i] || !std::isfinite(msg->effort[i])) return;
          std::lock_guard<std::mutex> lock(mutex_);
          for (int i = 0; i < 2; ++i) torque_[7 + i] = std::clamp(msg->effort[i], -35., 35.);
          gripCommandTime_ = rclcpp::Time(msg->header.stamp).seconds();
        });
      gripReadySub_ = node_->create_subscription<std_msgs::msg::Bool>(
        "/robot/gripper_ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
          std::lock_guard<std::mutex> lock(mutex_);
          gripReady_ = msg->data;
        });
    }
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
        reply->success = ready_ && cameras_ && (!gripper_ || gripReady_) && !launched_;
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
      cup_ = gz::Link(arm.LinkByName(ecm, gripper_ ? "fr3_hand" : "catch_cup"));
      if (!cup_.Valid(ecm)) throw std::runtime_error("Named tool link not preserved in SDF");
      ball_.EnableVelocityChecks(ecm);
      cup_.EnableVelocityChecks(ecm);
      for (size_t i = 0; i < (gripper_ ? 9u : 7u); ++i) {
        joints_[i] = arm.JointByName(ecm, names_[i]);
        if (!joints_[i]) throw std::runtime_error("Missing FR3 joint");
        ecm.CreateComponent(joints_[i], gz::components::JointPosition());
        ecm.CreateComponent(joints_[i], gz::components::JointVelocity());
        ecm.CreateComponent(
          joints_[i], gz::components::JointPositionReset({i < 7 ? initialQ_[i] : .04}));
        if (i >= 7)
          ecm.CreateComponent(
            joints_[i],
            gz::components::JointVelocityLimitsCmd({ignition::math::Vector2d(-.05, .05)}));
      }
      if (gripper_) {
        ballCollision_ = ecm.EntityByComponents(
          gz::components::Collision(), gz::components::ParentEntity(ball_.Entity()),
          gz::components::Name("ball"));
        // Fortress stores ContactSensorData on the watched collision, not on
        // the sensor entity. These data are privileged evaluation only.
        for (int i = 0; i < 2; ++i)
          contactShapes_[i] = ecm.EntityByComponents(
            gz::components::Collision(),
            gz::components::Name(
              i == 0 ? "fr3_leftfinger_collision_3" : "fr3_rightfinger_collision_3"));
        contactShapes_[2] = ballCollision_;
        for (auto entity : contactShapes_)
          if (!entity) throw std::runtime_error("Missing evaluation contact collision");
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
    for (size_t i = 0; i < (gripper_ ? 9u : 7u); ++i) {
      const double stamp = i < 7 ? commandTime_ : gripCommandTime_;
      const double torque = now - stamp <= 0.1 && stamp <= now + 0.002 ? torque_[i] : 0;
      ecm.SetComponentData<gz::components::JointForceCmd>(joints_[i], {torque});
    }
    if (launched_) {
      const auto velocity = ball_.WorldLinearVelocity(ecm);
      const auto pose = ball_.WorldPose(ecm);
      const double dt = std::chrono::duration<double>(info.dt).count();
      // Pair-specific empirical court impact. All robot/ball contacts remain
      // native Gazebo contacts. This impulse neither tracks nor attaches a ball.
      if (
        velocity && pose && bounceCount_ < allowedBounces_ && dt > 0 && velocity->Z() < 0 &&
        pose->Pos().Z() > .0335 &&
        pose->Pos().Z() + dt * velocity->Z() - .5 * 9.81 * dt * dt <= .0335 &&
        pose->Pos().X() > .0335 && pose->Pos().X() < 11.885 - .0335 &&
        std::abs(pose->Pos().Y()) < 8.23 / 2 - .0335) {
        const double hitTime =
          (velocity->Z() +
           std::sqrt(velocity->Z() * velocity->Z() + 2 * 9.81 * (pose->Pos().Z() - .0335))) /
          9.81;
        const Vector impactVelocity =
          *velocity + hitTime * (Vector(0, 0, -9.81) - drag_ * velocity->Length() * (*velocity));
        const Vector outgoing(
          courtTangentRatio_ * impactVelocity.X(), courtTangentRatio_ * impactVelocity.Y(),
          -courtRestitution_ * impactVelocity.Z());
        // Apply the contact impulse within one physics step of plane crossing.
        // Preserve the current position: no reset, attachment or target force.
        const Vector hitPosition = pose->Pos() + hitTime * (*velocity);
        ball_.SetLinearVelocity(ecm, outgoing);
        ++bounceCount_;
        lastBounceTime_ = now;
        if (bounces_) {
          bounces_ << std::setprecision(10) << "{\"time\":" << lastBounceTime_
                   << ",\"count\":" << bounceCount_ << ",\"position\":[" << hitPosition.X() << ','
                   << hitPosition.Y() << ",0.0335]"
                   << ",\"incoming_velocity\":[" << impactVelocity.X() << ',' << impactVelocity.Y()
                   << ',' << impactVelocity.Z() << "],\"outgoing_velocity\":[" << outgoing.X()
                   << ',' << outgoing.Y() << ',' << outgoing.Z() << "]}\n";
          bounces_.flush();
        }
      } else if (velocity) {
        ball_.AddWorldForce(ecm, -0.057 * drag_ * velocity->Length() * (*velocity));
      }
    }
    if (!launched_ && (requested_ || (auto_ && ready_ && cameras_ && (!gripper_ || gripReady_)))) {
      ballModel_.SetWorldPoseCmd(
        ecm, ignition::math::Pose3d(initial_, ignition::math::Quaterniond::Identity));
      ball_.SetLinearVelocity(ecm, velocity_);
      launched_ = true;
      launchTime_ = now;
      RCLCPP_INFO(node_->get_logger(), "Ball launched at simulation time %.3f", now);
    }
  }

  void PostUpdate(const gz::UpdateInfo & info, const gz::EntityComponentManager & ecm) override
  {
    if (!initialized_ || info.paused) return;
    const double now = std::chrono::duration<double>(info.simTime).count();
    if (gripper_) {
      contacts_.fill(false);
      for (int i = 0; i < 3; ++i) {
        const auto data = ecm.Component<gz::components::ContactSensorData>(contactShapes_[i]);
        if (!data) continue;
        for (const auto & contact : data->Data().contact()) {
          if (i < 2)
            contacts_[i] |= contact.collision1().id() == ballCollision_ ||
                            contact.collision2().id() == ballCollision_;
          else {
            const auto other = contact.collision1().id() == ballCollision_
                                 ? contact.collision2().id()
                                 : contact.collision1().id();
            auto parent = ecm.Component<gz::components::ParentEntity>(other);
            if (parent) {
              const auto name = ecm.Component<gz::components::Name>(parent->Data());
              if (launched_ && name && name->Data() == "floor" && now - lastBounceTime_ > .02) {
                floorTouched_ = true;
                if (floorTime_ < 0) floorTime_ = now;
              }
            }
          }
        }
      }
    }
    if (now - lastPublish_ < 0.004 - 1e-8) return;
    lastPublish_ = now;
    builtin_interfaces::msg::Time stamp = rclcpp::Time(static_cast<int64_t>(now * 1e9));
    sensor_msgs::msg::JointState state;
    state.header.stamp = stamp;
    for (size_t i = 0; i < (gripper_ ? 9u : 7u); ++i) {
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
    const auto ballVelocity = ball_.WorldLinearVelocity(ecm);
    const auto toolVelocity = cup_.WorldLinearVelocity(ecm);
    const auto toolAngular = cup_.WorldAngularVelocity(ecm);
    const Vector toolOffset =
      gripper_ ? cp->Rot().RotateVector(Vector(0, 0, .10365)) : Vector::Zero;
    const Vector center = cp->Pos() + toolOffset;
    const Vector relative = cp->Rot().Inverse().RotateVector(bp->Pos() - center);
    constexpr double ballRadius = 0.0335;
    // Match the actual 20-sided cavity, not an inscribed circle that wrongly
    // reports a ball resting against a polygon wall as repeatedly leaving.
    // A 2 mm tolerance accounts for contact solver penetration, far below the
    // ball radius. Vertical escape still resets the retention interval.
    const Vector bv = ballVelocity.value_or(Vector::Zero);
    const Vector tv =
      toolVelocity.value_or(Vector::Zero) + toolAngular.value_or(Vector::Zero).Cross(toolOffset);
    const bool inside =
      gripper_ ? physicallyGrasped(
                   relative.X(), relative.Y(), relative.Z(), (bv - tv).Length(), bp->Pos().Z(),
                   contacts_[0], contacts_[1], floorTouched_)
               : contained(relative.X(), relative.Y(), relative.Z(), radius_, depth_, ballRadius);
    bool launched;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      launched = launched_;
    }
    if (samples_)
      samples_ << std::setprecision(10) << now << ',' << bp->Pos().X() << ',' << bp->Pos().Y()
               << ',' << bp->Pos().Z() << ',' << cp->Pos().X() << ',' << cp->Pos().Y() << ','
               << cp->Pos().Z() << ',' << cp->Rot().X() << ',' << cp->Rot().Y() << ','
               << cp->Rot().Z() << ',' << cp->Rot().W() << ',' << inside << ',' << launched << ','
               << bv.X() << ',' << bv.Y() << ',' << bv.Z() << ',' << contacts_[0] << ','
               << contacts_[1] << ',' << bounceCount_;
    if (samples_) {
      for (double q : state.position) samples_ << ',' << q;
      samples_ << '\n';
    }
    if (finished_ && present_ && !presentationFinished_ && now >= presentationEnd_) {
      presentationFinished_ = true;
      if (!output_.empty()) {
        std::ofstream file(output_ + "/presentation_done.json");
        file << std::boolalpha << "{\"time\":" << now << ",\"held\":" << inside << "}\n";
      }
      if (samples_) samples_.flush();
    }
    if (!launched || finished_) return;
    if (inside && firstRetention_ < 0) firstRetention_ = now;
    if (gripper_ && bp->Pos().Z() <= ballRadius && floorTime_ < 0 && now - lastBounceTime_ > .02) {
      floorTouched_ = true;
      floorTime_ = now;
    }
    if (retained_.Update(inside, now, retention_))
      Finish(true, "retained", now);
    else if (gripper_ && floorTouched_)
      Finish(false, "floor_contact", now);
    else if (now - launchTime_ >= timeout_)
      Finish(false, "not_retained", now);
  }

private:
  void Finish(bool success, const std::string & reason, double now)
  {
    finished_ = true;
    presentationEnd_ = success && present_ ? now + 2.2 : now;
    std::ostringstream json;
    json << std::boolalpha << "{\"success\":" << success << ",\"reason\":\"" << reason
         << "\",\"launch_time\":" << launchTime_ << ",\"result_time\":" << now
         << ",\"retention\":" << retention_ << "}";
    auto text = json.str();
    text.pop_back();
    text += ",\"flight_end_time\":" + std::to_string(
                                        success           ? now - retention_
                                        : floorTime_ >= 0 ? floorTime_
                                                          : now);
    text += ",\"bounce_count\":" + std::to_string(bounceCount_);
    text += ",\"allowed_bounces\":" + std::to_string(allowedBounces_);
    text += ",\"presentation_end_time\":" + std::to_string(presentationEnd_);
    text += ",\"flight_end_kind\":\"" +
            std::string(
              success           ? "stable_capture"
              : floorTime_ >= 0 ? "floor_contact"
                                : "timeout") +
            "\"}";
    std_msgs::msg::String message;
    message.data = text;
    result_->publish(message);
    if (samples_) samples_.flush();
    if (!output_.empty()) {
      std::ofstream file(output_ + "/result.json");
      file << message.data << '\n';
    }
    RCLCPP_INFO(node_->get_logger(), "Catch result: %s", message.data.c_str());
  }
  std::array<std::string, 9> names_{"fr3_joint1", "fr3_joint2",        "fr3_joint3",
                                    "fr3_joint4", "fr3_joint5",        "fr3_joint6",
                                    "fr3_joint7", "fr3_finger_joint1", "fr3_finger_joint2"};
  std::array<double, 9> limits_{87, 87, 87, 87, 12, 12, 12, 35, 35};
  std::array<double, 7> initialQ_{};
  std::array<double, 9> torque_{};
  std::array<gz::Entity, 9> joints_{};
  std::array<gz::Entity, 3> contactShapes_{};
  std::array<bool, 2> contacts_{};
  gz::Entity ballCollision_{};
  gz::Model ballModel_;
  gz::Link ball_, cup_;
  Vector initial_, velocity_;
  double retention_{1}, radius_{0.12}, depth_{0.14}, timeout_{4};
  double commandTime_{-1}, launchTime_{0}, lastPublish_{-1};
  double gripCommandTime_{-1}, drag_{0}, floorTime_{-1}, firstRetention_{-1};
  bool gripper_{false}, gripReady_{false}, floorTouched_{false}, present_{false};
  double presentationEnd_{0};
  bool presentationFinished_{false};
  int allowedBounces_{0}, bounceCount_{0};
  double courtRestitution_{.745}, courtTangentRatio_{.6}, lastBounceTime_{-1};
  Retention retained_;
  bool initialized_{false}, auto_{true}, ready_{false}, cameras_{false}, requested_{false};
  bool launched_{false}, finished_{false};
  std::mutex mutex_;
  std::string output_;
  std::ofstream samples_, bounces_;
  rclcpp::Context::SharedPtr context_;
  rclcpp::Node::SharedPtr node_;
  rclcpp::executors::SingleThreadedExecutor::SharedPtr executor_;
  std::thread thread_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr state_;
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr truth_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr result_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr command_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr gripCommand_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr gripReadySub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr readySub_, cameraSub_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
};
}  // namespace ball_catching
IGNITION_ADD_PLUGIN(
  ball_catching::Host, ignition::gazebo::System, ball_catching::Host::ISystemConfigure,
  ball_catching::Host::ISystemPreUpdate, ball_catching::Host::ISystemPostUpdate)
