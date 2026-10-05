#include "ball_catching_robot/runtime.hpp"
#include <geometry_msgs/msg/vector3_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <trajectory_msgs/msg/joint_trajectory.hpp>
#include <limits>

namespace ball_catching {
class Intercept : public rclcpp::Node {
 public:
  Intercept() : Node("ball_intercept"), arm_(readFile(declare_parameter<std::string>("robot_file", ""))),
      record_(declare_parameter<std::string>("output", ""), "interception") {
    const auto home = jointVector(declare_parameter<std::vector<double>>("initial_pose",
        {0., -.7853981633974483, 0., -2.356194490192345, 0., 1.5707963267948966, .7853981633974483}));
    arm_.validatePose(home);
    const auto pose = arm_.pose(home); rotation_ = pose.M; height_ = pose.p.z();
    commands_ = create_publisher<trajectory_msgs::msg::JointTrajectory>("/robot/joint_trajectory", 1);
    joints_ = create_subscription<sensor_msgs::msg::JointState>("/robot/joint_states", rclcpp::SensorDataQoS(),
        [this](sensor_msgs::msg::JointState::ConstSharedPtr msg) { feedback_.update(*msg, arm_.names); });
    observations_ = create_subscription<nav_msgs::msg::Odometry>("/robot/ball_state", rclcpp::SensorDataQoS(),
        [this](nav_msgs::msg::Odometry::ConstSharedPtr msg) {
          if (msg->header.frame_id != "world") return;
          const auto &p = msg->pose.pose.position; const auto &v = msg->twist.twist.linear;
          Ball ball{seconds(msg->header.stamp), Vec3(p.x, p.y, p.z), Vec3(v.x, v.y, v.z)};
          if (ball.position.allFinite() && ball.velocity.allFinite()) ball_ = ball;
        });
    geometry_ = create_subscription<geometry_msgs::msg::Vector3Stamped>("/robot/ball_geometry", rclcpp::SensorDataQoS(),
        [this](geometry_msgs::msg::Vector3Stamped::ConstSharedPtr msg) {
          if (msg->vector.x > .005 && msg->vector.x < .15)
            radius_ = radius_ ? .2*msg->vector.x + .8*(*radius_) : msg->vector.x;
        });
    timer_ = timer(*this, 1./30, [this] { plan(); });
  }
 private:
  struct Ball { double time; Vec3 position, velocity; };
  void plan() {
    const auto stamp = now();
    const double time = stamp.seconds();
    if (!feedback_.valid || !ball_ || !radius_ || time < holdUntil_) return;
    const double age = time - ball_->time;
    if (age < 0 || age > .1 || time - feedback_.time > .05 || time - lastPlan_ < .06) return;
    const Vec3 position = ball_->position + ball_->velocity*age + .5*gravity*age*age;
    const Vec3 velocity = ball_->velocity + gravity*age;
    const auto cup = arm_.pose(feedback_.q).p;
    if (position.z() < cup.z() + .025 && (position.head<2>() - Eigen::Vector2d(cup.x(), cup.y())).norm() < .13) {
      // Visual entry triggers holding; privileged scoring never enters control.
      holdUntil_ = time + 2.; return;
    }
    const auto started = WallClock::now();
    const MotionState initial = trajectory_ ? trajectory_->sample(time) : MotionState{feedback_.q, feedback_.dq, Vec7::Zero()};
    std::optional<Quintic> best;
    Vec3 bestPoint;
    Vec7 bestTarget;
    double score = std::numeric_limits<double>::infinity();
    for (double height : {height_, height_ + .10, height_ - .10, height_ - .20, height_ + .20}) {
      const double discriminant = velocity.z()*velocity.z() + 2*9.81*(position.z() - height - *radius_ - .005);
      if (discriminant < 0) continue;
      const double dt = (velocity.z() + std::sqrt(discriminant))/9.81;
      if (dt <= .07 || dt >= 1.2) continue;
      Vec3 point = position + velocity*dt + .5*gravity*dt*dt; point.z() = height;
      const double distance = point.head<2>().norm();
      if (distance <= .12 || distance >= 1.10 || height <= .20 || height >= 1.30) continue;
      const auto target = arm_.inverse(point, rotation_, feedback_.q);
      if (!target) continue;
      Quintic trajectory(time, dt, initial, *target);
      const double cost = (*target - initial.q).norm();
      if (cost < score && trajectory.feasible(arm_)) {
        score = cost; best = trajectory; bestPoint = point; bestTarget = *target;
      }
    }
    if (!best) {
      record_.write({{"time", time}, {"capture_time", ball_->time}, {"status", "no_feasible_intercept"},
                     {"planning_wall_seconds", elapsed(started)}}); return;
    }
    trajectory_ = best; lastPlan_ = time;
    trajectory_msgs::msg::JointTrajectory msg;
    // Keep planning time despite computation/transport delays.
    msg.header.stamp = stamp; msg.joint_names = arm_.names;
    trajectory_msgs::msg::JointTrajectoryPoint first, last;
    first.positions = values(initial.q); first.velocities = values(initial.dq); first.accelerations = values(initial.ddq);
    last.positions = values(bestTarget); last.velocities = values(Vec7::Zero().eval()); last.accelerations = last.velocities;
    last.time_from_start = rclcpp::Duration::from_seconds(best->duration);
    msg.points = {first, last}; commands_->publish(msg);
    record_.write({{"time", time}, {"capture_time", ball_->time}, {"status", "planned"},
                   {"target", values(bestPoint)}, {"target_joints", values(bestTarget)},
                   {"intercept_time", time + best->duration}, {"planning_wall_seconds", elapsed(started)}});
  }
  Arm arm_;
  Recorder record_;
  Feedback feedback_;
  KDL::Rotation rotation_;
  double height_, lastPlan_{-1}, holdUntil_{-1};
  std::optional<Ball> ball_;
  std::optional<double> radius_;
  std::optional<Quintic> trajectory_;
  rclcpp::Publisher<trajectory_msgs::msg::JointTrajectory>::SharedPtr commands_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr observations_;
  rclcpp::Subscription<geometry_msgs::msg::Vector3Stamped>::SharedPtr geometry_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}
int main(int argc, char **argv) { return ball_catching::run<ball_catching::Intercept>(argc, argv); }
