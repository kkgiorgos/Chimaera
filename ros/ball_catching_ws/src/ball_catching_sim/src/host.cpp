// Host-only physics adapter and privileged scorer. No trajectory tracking here.
#include <algorithm>
#include <array>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <ignition/gazebo/EventManager.hh>
#include <ignition/gazebo/Events.hh>
#include <ignition/gazebo/Link.hh>
#include <ignition/gazebo/Model.hh>
#include <ignition/gazebo/System.hh>
#include <ignition/gazebo/components/Gravity.hh>
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
#include <json-c/json.h>
#include <mutex>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <set>
#include <sstream>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <thread>
#include <vector>

#include "ball_catching_sim/detailed_start.hpp"
#include "ball_catching_sim/command_replay.hpp"
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
    const gz::Entity & world, const std::shared_ptr<const sdf::Element> & sdf,
    gz::EntityComponentManager & ecm, gz::EventManager & events) override
  {
    events_ = &events;
    initial_ = sdf->Get<Vector>("launch_position");
    velocity_ = sdf->Get<Vector>("launch_velocity");
    if (const auto gravity = ecm.Component<gz::components::Gravity>(world))
      gravity_ = gravity->Data();
    radius_ = sdf->Get<double>("cup_radius");
    depth_ = sdf->Get<double>("cup_depth");
    duration_ = sdf->Get<double>("duration");
    score_.emplace(duration_);
    auto_ = sdf->Get<bool>("auto_throw");
    feedbackPeriod_ = 1. / (sdf->HasElement("joint_hz") ? sdf->Get<double>("joint_hz") : 250.);
    detailedSocket_ = sdf->HasElement("detailed_timing_socket") ?
      sdf->Get<std::string>("detailed_timing_socket") : "";
    checkpointGate_ = sdf->HasElement("checkpoint_gate") && sdf->Get<bool>("checkpoint_gate");
    reconnectAfterNs_ = sdf->HasElement("checkpoint_live_after_ns") ?
      std::stoll(sdf->Get<std::string>("checkpoint_live_after_ns")) : -1;
    if (reconnectAfterNs_ < -1 || (reconnectAfterNs_ >= 0 && !checkpointGate_))
      throw std::runtime_error("Invalid checkpoint reconnection boundary");
    const auto capture = sdf->HasElement("checkpoint_commands_capture") ?
      sdf->Get<std::string>("checkpoint_commands_capture") : "";
    const auto replay = sdf->HasElement("checkpoint_commands_replay") ?
      sdf->Get<std::string>("checkpoint_commands_replay") : "";
    if ((!capture.empty() && !replay.empty()) ||
        ((!capture.empty() || !replay.empty()) && !checkpointGate_))
      throw std::runtime_error("Command replay requires a pre-throw checkpoint gate");
    if (!capture.empty() || !replay.empty())
      commandReplay_ = std::make_unique<CommandReplay>(capture.empty() ? replay : capture, !capture.empty());
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
      readiness_.open(output_ + "/readiness.csv");
      if (!readiness_) throw std::runtime_error("Cannot open readiness output");
      readiness_ << "time,arm_ready,perception_ready,warmup_elapsed,warmup_required\n"
                 << std::setprecision(17);
      guestRecords_.open(output_ + "/guest_records.jsonl");
      if (!guestRecords_) throw std::runtime_error("Cannot open guest telemetry output");
      if (!replay.empty()) {
        std::ifstream prefix(std::filesystem::path(replay).parent_path() / "guest_records.jsonl");
        if (!prefix) throw std::runtime_error("Checkpoint lacks its completed telemetry prefix");
        std::string line;
        while (std::getline(prefix, line)) guestRecords_ << line << '\n';
        guestRecords_.flush();
        if (!prefix.eof() || !guestRecords_) throw std::runtime_error("Cannot restore telemetry prefix");
      }
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
    for (const auto * stream : {"perception", "interception", "control", "control_timing"}) {
      recordsSubs_.push_back(node_->create_subscription<std_msgs::msg::String>(
        std::string("/benchmark/") + stream, rclcpp::QoS(128).reliable(),
        [this, stream = std::string(stream)](std_msgs::msg::String::ConstSharedPtr msg) {
          if (reconnectAfterNs_ >= 0) {
            std::unique_ptr<json_object, decltype(&json_object_put)> root(
              json_tokener_parse(msg->data.c_str()), json_object_put);
            json_object * probe = nullptr;
            if (root && json_object_object_get_ex(root.get(), "checkpoint_probe", &probe) &&
                json_object_is_type(probe, json_type_string) &&
                std::string(json_object_get_string(probe)) == "chimaera-record-resume-v1") {
              checkpointRecordReaders_.insert(stream);
              if (checkpointRecordReaders_.size() == 4) {
                std::ofstream marker(output_ + "/checkpoint-record-readers-ready");
                marker << '\n'; marker.flush();
                if (!marker) throw std::runtime_error("Cannot save record reader readiness");
              }
              return; // Protocol metadata never enters the benchmark record journal.
            }
          }
          // Persist on the host; no guest disk I/O or serial-console transfer.
          if (guestRecords_) {
            guestRecords_ << msg->data << '\n';
            guestRecords_.flush();
          }
          if (reconnectAfterNs_ >= 0 && stream == "perception") {
            std::unique_ptr<json_object, decltype(&json_object_put)> root(
              json_tokener_parse(msg->data.c_str()), json_object_put);
            json_object * record = nullptr, * capture = nullptr, * status = nullptr;
            if (root && json_object_object_get_ex(root.get(), "record", &record) &&
                json_object_object_get_ex(record, "capture_time", &capture)) {
              const double time = json_object_get_double(capture);
              const bool expired = json_object_object_get_ex(record, "status", &status) &&
                json_object_is_type(status, json_type_string) &&
                std::string(json_object_get_string(status)) == "expired_processing";
              if (std::isfinite(time) && time * 1e9 > reconnectAfterNs_) {
                std::lock_guard<std::mutex> lock(mutex_);
                ++newStereoRecords_;
                if (expired) ++expiredStereoRecords_;
                else liveCamera_ = true;
              }
            }
          }
        }));
    }
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
        if (reconnectAfterNs_ >= 0 && rclcpp::Time(msg->header.stamp).nanoseconds() > reconnectAfterNs_)
          liveControl_ = true;
        ObserveCheckpointCommand();
        if (commands_) {
          commands_ << worldTime_ << ',' << commandTime_ << '\n';
          commands_.flush();
        }
      });
    readySub_ = node_->create_subscription<std_msgs::msg::Bool>(
      "/robot/ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
        std::lock_guard<std::mutex> lock(mutex_);
        ready_ = msg->data;
        ObserveCheckpointCommand();
      });
    cameraSub_ = node_->create_subscription<std_msgs::msg::Bool>(
      "/robot/perception_ready", 1, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
        std::lock_guard<std::mutex> lock(mutex_);
        cameras_ = msg->data;
        ObserveCheckpointCommand();
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
    const double now = std::chrono::duration<double>(info.simTime).count();
    // Physics applies commands even during paused updates. After the launch
    // step it retains the pose command and zeros the velocity command. Clear
    // both before the first paused callback can reset the free ballistic flight.
    if (launched_ && (info.paused || now > launchTime_)) {
      ecm.RemoveComponent<gz::components::LinearVelocityCmd>(ball_.Entity());
      ecm.RemoveComponent<gz::components::WorldPoseCmd>(ballModel_.Entity());
    }
    if (info.paused) return;
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
    if (commandReplay_ && !launched_) {
      CommandState state{torque_, commandTime_, ready_, cameras_};
      commandReplay_->apply(std::chrono::duration_cast<std::chrono::nanoseconds>(info.simTime).count(), state);
      torque_ = state.torque; commandTime_ = state.stamp;
      ready_ = state.ready; cameras_ = state.cameras;
      if (commandReplay_->replay_complete())
        std::ofstream(output_ + "/checkpoint-commands-replayed").put('\n');
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
      readiness_ &&
      (now - lastReadiness_ >= .05 || ready_ != loggedReady_ || cameras_ != loggedCameras_)) {
      readiness_ << now << ',' << ready_ << ',' << cameras_ << ','
                 << (readySince_ >= 0 ? now - readySince_ : 0.) << ',' << warmup_ << '\n';
      readiness_.flush();
      lastReadiness_ = now;
      loggedReady_ = ready_;
      loggedCameras_ = cameras_;
    }
    const bool readyToLaunch = readySince_ >= 0 && now - readySince_ + 1e-8 >= warmup_;
    if (reconnectAfterNs_ >= 0 && !reconnected_ && now - lastReconnectStatus_ >= .05) {
      const auto path = output_ + "/checkpoint-reconnect-status.json";
      std::ofstream status(path + ".tmp");
      status << std::boolalpha << "{\"live_control\":" << liveControl_
             << ",\"arm_ready\":" << ready_ << ",\"perception_ready\":" << cameras_
             << ",\"live_camera\":" << liveCamera_
             << ",\"new_stereo_records\":" << newStereoRecords_
             << ",\"expired_stereo_records\":" << expiredStereoRecords_ << "}\n";
      status.close();
      if (!status) throw std::runtime_error("Cannot save checkpoint readiness diagnostics");
      std::filesystem::rename(path + ".tmp", path);
      lastReconnectStatus_ = now;
    }
    if (reconnectAfterNs_ >= 0 && !reconnected_ && readyToLaunch && liveControl_ && liveCamera_) {
      std::ofstream marker(output_ + "/checkpoint-reconnected");
      marker << '\n'; marker.flush();
      if (!marker) throw std::runtime_error("Cannot save checkpoint reconnection marker");
      reconnected_ = true;
    }
    if (checkpointGate_ && !output_.empty()) {
      const auto marker = output_ + "/checkpoint-ready";
      if (readyToLaunch) {
        if (!std::filesystem::exists(marker)) std::ofstream(marker).put('\n');
      } else {
        std::filesystem::remove(marker);
      }
    }
    const bool released =
      !checkpointGate_ || std::filesystem::exists(output_ + "/checkpoint-release");
    // The checkpoint already passed readiness without interference. Pressure
    // warmup has a fixed duration; do not extend it by gating on readiness again.
    if (!launched_ && (checkpointGate_ || readyToLaunch) && released && (requested_ || auto_)) {
      // Both simulators are paused while measurement statistics reset.
      beginDetailedThrow(detailedSocket_);
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
    if (commandReplay_) {
      std::lock_guard<std::mutex> lock(mutex_);
      CommandState tail;
      if (commandReplay_->finish_replay(tail)) {
        torque_ = tail.torque; commandTime_ = tail.stamp;
        ready_ = tail.ready; cameras_ = tail.cameras;
      }
    }
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
    const Vector bv = ball_.WorldLinearVelocity(ecm).value_or(Vector::Zero);
    if (launched && !launchChecked_ && now > launchTime_) {
      launchChecked_ = true;
      const double step = std::chrono::duration<double>(info.dt).count();
      const double elapsed = now - launchTime_ + step;
      const Vector expectedVelocity = velocity_ + gravity_ * elapsed;
      const Vector expectedPosition =
        initial_ + velocity_ * elapsed + gravity_ * (.5 * elapsed * (elapsed + step));
      const bool valid =
        (bv - expectedVelocity).Length() <= .02 && (bp->Pos() - expectedPosition).Length() <= .002;
      if (!output_.empty()) {
        std::ofstream validation(output_ + "/launch_validation.json");
        validation << std::boolalpha << std::setprecision(17) << "{\"valid\":" << valid
                   << ",\"launch_time\":" << launchTime_ << ",\"checked_at\":" << now
                   << ",\"reason\":\""
                   << (valid ? "ballistic_launch_verified" : "launch_pose_or_velocity_reset")
                   << "\",\"expected_velocity_mps\":[" << expectedVelocity.X() << ','
                   << expectedVelocity.Y() << ',' << expectedVelocity.Z()
                   << "],\"observed_velocity_mps\":[" << bv.X() << ',' << bv.Y() << ',' << bv.Z()
                   << "],\"expected_position_m\":[" << expectedPosition.X() << ','
                   << expectedPosition.Y() << ',' << expectedPosition.Z()
                   << "],\"observed_position_m\":[" << bp->Pos().X() << ',' << bp->Pos().Y() << ','
                   << bp->Pos().Z() << "]}\n";
      }
      if (!valid) {
        finished_ = true;
        if (samples_) samples_.flush();
        events_->Emit<gz::events::Pause>(true);
        RCLCPP_ERROR(node_->get_logger(), "Invalid ball launch: pose or velocity reset");
        return;
      }
    }
    // Track containment at every physics step. Evaluate on the first step at
    // the deadline, even when it falls between normal 250 Hz feedback samples.
    const auto evaluation = launched ? score_->Update(inside, now, launchTime_) : std::nullopt;
    if (checkpointGate_ && !launched && !output_.empty()) SaveCheckpointState(ecm, now);
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
  // Call while holding mutex_. Preserve received commands not consumed by the
  // final replayed physics step, as well as the per-step applied prefix.
  void ObserveCheckpointCommand()
  {
    if (commandReplay_) commandReplay_->observe({torque_, commandTime_, ready_, cameras_});
  }
  void SaveCheckpointState(const gz::EntityComponentManager & ecm, double now) const
  {
    std::array<double, 7> positions{}, velocities{};
    for (size_t i = 0; i < joints_.size(); ++i) {
      const auto p = ecm.Component<gz::components::JointPosition>(joints_[i]);
      const auto v = ecm.Component<gz::components::JointVelocity>(joints_[i]);
      if (!p || !v || p->Data().empty() || v->Data().empty()) return;
      positions[i] = p->Data()[0];
      velocities[i] = v->Data()[0];
    }
    const auto path = output_ + "/checkpoint-host-state";
    {
      std::ofstream snapshot(path + ".tmp");
      snapshot << std::setprecision(17) << now;
      for (double q : positions) snapshot << ' ' << q;
      for (double v : velocities) snapshot << ' ' << v;
      snapshot << '\n';
      if (!snapshot) throw std::runtime_error("Cannot save checkpoint host state");
    }
    std::filesystem::rename(path + ".tmp", path);
  }

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
  Vector initial_, velocity_, gravity_{0, 0, -9.81};
  double duration_{1.5}, radius_{0.12}, depth_{0.14};
  double commandTime_{-1}, launchTime_{0}, lastPublish_{-1};
  double feedbackPeriod_{.004}, warmup_{0}, readySince_{-1}, worldTime_{0};
  std::ofstream commands_;
  std::string detailedSocket_;
  std::ofstream readiness_;
  double lastReadiness_{-1};
  bool loggedReady_{false}, loggedCameras_{false};
  std::optional<DeadlineScore> score_;
  bool initialized_{false}, auto_{true}, ready_{false}, cameras_{false}, requested_{false};
  bool launched_{false}, launchChecked_{false}, finished_{false};
  bool checkpointGate_{false};
  int64_t reconnectAfterNs_{-1};
  bool liveControl_{false}, liveCamera_{false}, reconnected_{false};
  uint64_t newStereoRecords_{}, expiredStereoRecords_{};
  double lastReconnectStatus_{-1};
  std::set<std::string> checkpointRecordReaders_;
  mutable std::mutex mutex_;
  std::unique_ptr<CommandReplay> commandReplay_;
  std::string output_;
  std::ofstream guestRecords_;
  std::vector<rclcpp::Subscription<std_msgs::msg::String>::SharedPtr> recordsSubs_;
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
