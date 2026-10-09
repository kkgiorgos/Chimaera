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
    record_(declare_parameter<std::string>("output", ""), "interception")
  {
    const auto home = jointVector(
      declare_parameter<std::vector<double>>("initial_pose", values(uprightHome(arm_.gripper))));
    arm_.validateHome(home);
    home_ = home;
    present_ = declare_parameter("present", false);
    presentationHold_ = declare_parameter("presentation_hold", .25);
    if (!std::isfinite(presentationHold_) || presentationHold_ < .25)
      throw std::invalid_argument("Presentation hold must be at least 250 ms");
    motorLead_ = declare_parameter("gripper_motor_lead", 0.);
    if (!std::isfinite(motorLead_) || motorLead_ < 0 || motorLead_ > .1)
      throw std::invalid_argument("Gripper motor lead must be 0–100 ms");
    const auto pose = arm_.pose(home);
    rotation_ = arm_.gripper ? pose.M : arm_.pose(uprightHome(true)).M;
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
        for (int i = 0; i < 3; ++i) {
          ball.positionVariance[i] = msg->pose.covariance[i * 6 + i];
          ball.velocityVariance[i] = msg->twist.covariance[i * 6 + i];
        }
        if (
          !ball.positionVariance.allFinite() || !ball.velocityVariance.allFinite() ||
          (ball.positionVariance.array() < 0).any() || (ball.velocityVariance.array() < 0).any())
          return;
        if (ball.position.allFinite() && ball.velocity.allFinite()) ball_ = ball;
      });
    geometry_ = create_subscription<geometry_msgs::msg::Vector3Stamped>(
      "/robot/ball_geometry", rclcpp::SensorDataQoS(),
      [this](geometry_msgs::msg::Vector3Stamped::ConstSharedPtr msg) {
        if (msg->vector.x > .005 && msg->vector.x < .15)
          radius_ = radius_ ? .2 * msg->vector.x + .8 * (*radius_) : msg->vector.x;
        if (std::isfinite(msg->vector.y) && msg->vector.y >= 0 && msg->vector.y <= .15)
          drag_ = msg->vector.y;
      });
    if (arm_.gripper) {
      gripper_ = create_publisher<geometry_msgs::msg::Vector3Stamped>("/robot/gripper_target", 1);
      grasp_ = create_subscription<geometry_msgs::msg::Vector3Stamped>(
        "/robot/gripper_state", rclcpp::SensorDataQoS(),
        [this](geometry_msgs::msg::Vector3Stamped::ConstSharedPtr msg) {
          grasped_ = msg->vector.y > .5;
          if (grasped_ && graspTime_ < 0) graspTime_ = now().seconds();
        });
    }
    timer_ = timer(*this, 1. / 30, [this] { plan(); });
  }

private:
  struct Ball
  {
    double time;
    Vec3 position, velocity;
    Vec3 positionVariance = Vec3::Zero(), velocityVariance = Vec3::Zero();
  };
  void plan()
  {
    const auto stamp = now();
    const double time = stamp.seconds();
    if (arm_.gripper) {
      planGrasp(time);
      return;
    }
    if (
      !feedback_.valid || !ball_ || !radius_ || time < holdUntil_ ||
      (cupInterceptTime_ > 0 && time >= cupInterceptTime_ - .025))
      return;
    const double age = time - ball_->time;
    if (age < 0 || age > .1 || time - feedback_.time > .05 || time - lastPlan_ < .06) return;
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
    const auto started = WallClock::now();
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
      if (!target) target = arm_.inverse(point, rotation_, uprightHome(true), 120);
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
         {"planning_wall_seconds", elapsed(started)}});
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
       {"planning_wall_seconds", elapsed(started)}});
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
    graspTrajectories_ = segments;
  }
  MotionState desired(double time) const
  {
    MotionState s{home_, Vec7::Zero(), Vec7::Zero()};
    for (const auto & segment : graspTrajectories_) {
      s = segment.sample(time);
      if (time <= segment.start + segment.duration) break;
    }
    return s;
  }
  void prepareApproach(double time)
  {
    if (approached_ || !ball_ || ball_->position.x() < 1.5 || ball_->velocity.x() > -.5) return;
    const auto started = WallClock::now();
    // A visually detected incoming ball permits a coarse approach before its
    // precise intercept is known. The workspace centre is a robot geometry
    // prior; no launch target, bounce event or evaluation feedback is used.
    const Vec3 bearing = Vec3(ball_->position.x(), ball_->position.y(), 0.).normalized();
    const Vec3 point = .5 * bearing + Vec3(0., 0., .8);
    const Vec3 z = std::sqrt(.75) * bearing + Vec3(0., 0., .5);
    const Vec3 x = Vec3::UnitY().cross(z).normalized(), y = z.cross(x);
    const KDL::Rotation rotation(
      KDL::Vector(x.x(), x.y(), x.z()), KDL::Vector(y.x(), y.y(), y.z()),
      KDL::Vector(z.x(), z.y(), z.z()));
    std::optional<Vec7> target;
    double margin = -1.;
    for (double shoulder : {-1., 0., 1.})
      for (double elbow : {-1., 0., 1.})
        for (double wrist : {-1.5, 0., 1.5}) {
          Vec7 seed = feedback_.q;
          seed[0] = shoulder;
          seed[2] = elbow;
          seed[4] = wrist;
          const auto candidate = arm_.inverse(point, rotation, seed, 300);
          if (!candidate) continue;
          const double candidateMargin =
            (*candidate - arm_.lower).cwiseMin(arm_.upper - *candidate).minCoeff();
          if (candidateMargin > margin + 1e-5) {
            target = candidate;
            margin = candidateMargin;
          }
        }
    approached_ = true;
    if (target) {
      for (double duration = .8; duration <= 2.4; duration += .1) {
        Quintic approach(time, duration, desired(time), *target);
        if (!approach.feasible(arm_)) continue;
        publish({approach});
        record_.write(
          {{"time", time},
           {"capture_time", ball_->time},
           {"status", "visual_approach"},
           {"target", values(point)},
           {"target_joints", values(*target)},
           {"duration", duration},
           {"planning_wall_seconds", elapsed(started)}});
        return;
      }
    }
    record_.write(
      {{"time", time},
       {"status", "visual_approach_unreachable"},
       {"planning_wall_seconds", elapsed(started)}});
  }
  void planGrasp(double time)
  {
    if (!feedback_.valid || time - feedback_.time < 0 || time - feedback_.time > .05) return;
    if (grasped_) {
      if (present_ && !presented_ && time - graspTime_ > presentationHold_) {
        const auto started = WallClock::now();
        const auto pose = arm_.pose(feedback_.q);
        const auto rotation =
          KDL::Rotation(KDL::Vector(0, 0, 1), KDL::Vector(0, 1, 0), KDL::Vector(-1, 0, 0));
        const auto turn = (pose.M.Inverse() * rotation).GetRot();
        const double angle = turn.Norm();
        bool showing = false;
        for (double fraction : {1., .5, 0.}) {
          const auto displayRotation =
            angle > 1e-8 ? pose.M * KDL::Rotation::Rot(turn / angle, angle * fraction) : pose.M;
          for (const Vec3 & point :
               {Vec3(.45, 0., .9),
                Vec3(pose.p.x(), pose.p.y(), std::min(1.15, pose.p.z() + .10))}) {
            const auto q = arm_.inverse(point, displayRotation, feedback_.q, 150);
            if (!q) continue;
            Quintic show(time, 2., desired(time), *q);
            if (show.feasible(arm_)) {
              publish({show});
              showing = true;
              break;
            }
          }
          if (showing) break;
        }
        presented_ = true;
        record_.write(
          {{"time", time},
           {"status", showing ? "presenting" : "presentation_unreachable"},
           {"planning_wall_seconds", elapsed(started)}});
      }
      return;
    }
    if (interceptTime_ > 0 && time > interceptTime_ + .15) {
      // The hardware never reported a held object. Release an expired attempt
      // so a later visually observed flight can still be intercepted.
      interceptTime_ = -1;
      geometry_msgs::msg::Vector3Stamped open;
      open.header.stamp = now();
      open.vector.x = .08;
      gripper_->publish(open);
      record_.write({{"time", time}, {"status", "missed_intercept_retry"}});
    }
    if (
      !ball_ || !radius_ || *radius_ >= .04 || time - lastPlan_ < .05 ||
      (interceptTime_ > 0 && time > interceptTime_ - .025))
      return;
    const double age = time - ball_->time;
    if (age < 0 || age > .15) return;
    prepareApproach(time);
    const auto started = WallClock::now();
    Vec6 captured;
    captured << ball_->position, ball_->velocity;
    const Vec6 current = propagateBall(captured, age, drag_);
    const MotionState initial = desired(time);
    const auto waitingPose = arm_.pose(initial.q);
    std::vector<Quintic> best;
    Vec3 bestPoint = Vec3::Zero();
    double bestTime = 0, score = std::numeric_limits<double>::infinity();
    std::vector<double> times;
    for (double dt = .12; dt <= 1.4; dt += .035) times.push_back(dt);
    // Include flight through the current commanded grasp height. Upright home
    // is only the start pose; a visual approach changes this waiting height.
    const double waitingHeight = waitingPose.p.z();
    double previousHeight = current.z();
    Vec6 heightState = current;
    for (double dt = .01; dt <= 1.4; dt += .01) {
      heightState = propagateBall(heightState, .01, drag_);
      const double z = heightState.z();
      if (
        (z - waitingHeight) * (previousHeight - waitingHeight) <= 0 &&
        std::abs(z - previousHeight) > 1e-8) {
        const double crossing =
          dt - .01 + .01 * (waitingHeight - previousHeight) / (z - previousHeight);
        if (crossing >= .09) times.push_back(crossing);
      }
      previousHeight = z;
    }
    int inverseSolutions = 0, safeApproaches = 0;
    int uncertainPredictions = 0;
    for (double dt : times) {
      // A conservative projection without unavailable cross-covariance. This
      // prevents a tiny distant image from committing an early wrist motion.
      const double sigma = std::sqrt(ball_->positionVariance.sum()) +
                           (age + dt) * std::sqrt(ball_->velocityVariance.sum());
      if (sigma > .06) {
        ++uncertainPredictions;
        continue;
      }
      const Vec6 predicted = propagateBall(current, dt, drag_);
      const Vec3 point = predicted.head<3>(), velocity = predicted.tail<3>();
      if (
        point.z() < .25 || point.z() > 1.25 || point.head<2>().norm() < .15 ||
        point.head<2>().norm() > .95 || point.x() < .1 || velocity.norm() < .5)
        continue;
      const Vec3 z = -velocity.normalized();
      Vec3 x = Vec3::UnitY().cross(z).normalized();
      if (!x.allFinite()) continue;
      const Vec3 y = z.cross(x);
      const KDL::Rotation aligned(
        KDL::Vector(x.x(), x.y(), x.z()), KDL::Vector(y.x(), y.y(), y.z()),
        KDL::Vector(z.x(), z.y(), z.z()));
      std::vector<KDL::Rotation> rotations{
        aligned, aligned * KDL::Rotation::RotZ(1.5707963267948966)};
      const KDL::Vector jaw = waitingPose.M * KDL::Vector(0, 1, 0);
      if (std::abs(Vec3(jaw.x(), jaw.y(), jaw.z()).dot(velocity.normalized())) < .2)
        rotations.push_back(waitingPose.M);
      for (const auto & rotation : rotations) {
        const auto q = arm_.inverse(point, rotation, feedback_.q, 70);
        if (!q) continue;
        ++inverseSolutions;
        const auto jacobian = arm_.jacobian(*q);
        for (double matchingSpeed : {.8, .3, 0.}) {
          Vec6 twist = Vec6::Zero();
          twist.head<3>() = velocity * std::min(1., matchingSpeed / velocity.norm());
          Vec7 dq = jacobian.transpose() *
                    (jacobian * jacobian.transpose() + .001 * Mat6::Identity()).ldlt().solve(twist);
          double scale = 1.;
          for (int i = 0; i < 7; ++i)
            scale = std::min(scale, .6 * arm_.velocity[i] / std::max(std::abs(dq[i]), 1e-9));
          dq *= scale;
          const MotionState caught{*q, dq, Vec7::Zero()};
          Quintic approach(time, dt, initial, caught);
          const double brakeTime = .3;
          Quintic brake(time + dt, brakeTime, caught, (*q + dq * brakeTime / 2).eval());
          const double cost = (*q - initial.q).norm() + .1 * dt + .02 * (.8 - matchingSpeed);
          const bool safe = approach.feasible(arm_);
          if (safe) ++safeApproaches;
          if (cost < score && safe && brake.feasible(arm_)) {
            score = cost;
            best = {approach, brake};
            bestPoint = point;
            bestTime = time + dt;
          }
        }
      }
    }
    lastPlan_ = time;
    if (best.empty()) {
      record_.write(
        {{"time", time},
         {"capture_time", ball_->time},
         {"status", "no_feasible_intercept"},
         {"inverse_solutions", static_cast<double>(inverseSolutions)},
         {"safe_approaches", static_cast<double>(safeApproaches)},
         {"uncertain_predictions", static_cast<double>(uncertainPredictions)},
         {"planning_wall_seconds", elapsed(started)}});
      return;
    }
    publish(best);
    interceptTime_ = bestTime;
    geometry_msgs::msg::Vector3Stamped close;
    const double objectWidth = 2 * (*radius_);
    const double closeTime = bestTime - std::max(0., .08 - objectWidth) / .1 - motorLead_;
    close.header.stamp = rclcpp::Time(static_cast<int64_t>(closeTime * 1e9));
    close.vector.x = std::max(0., objectWidth - .006);
    close.vector.y = objectWidth;
    gripper_->publish(close);
    record_.write(
      {{"time", time},
       {"capture_time", ball_->time},
       {"status", "planned"},
       {"target", values(bestPoint)},
       {"intercept_time", bestTime},
       {"close_time", closeTime},
       {"object_width", objectWidth},
       {"motor_lead", motorLead_},
       {"drag", drag_},
       {"planning_wall_seconds", elapsed(started)}});
  }
  Arm arm_;
  Recorder record_;
  Feedback feedback_;
  Vec7 home_;
  KDL::Rotation rotation_;
  double height_, lastPlan_{-1}, holdUntil_{-1};
  std::optional<Ball> ball_;
  std::optional<double> radius_;
  std::vector<Quintic> graspTrajectories_;
  double cupInterceptTime_{-1};
  double motorLead_{0}, presentationHold_{.25};
  double drag_{0}, interceptTime_{-1}, graspTime_{-1};
  bool grasped_{false}, present_{false}, presented_{false}, approached_{false};
  rclcpp::Publisher<geometry_msgs::msg::Vector3Stamped>::SharedPtr gripper_;
  rclcpp::Subscription<geometry_msgs::msg::Vector3Stamped>::SharedPtr grasp_;
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
