#include <fcntl.h>
#include <sys/resource.h>
#include <sys/utsname.h>
#include <unistd.h>

#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <geometry_msgs/msg/twist.hpp>
#include <iomanip>
#include <iostream>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/create_timer.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>
#include <sstream>

#include "wall_follow_benchmark/core.hpp"

#define DOUBLE_PARAMS(X)                                                                          \
  X(control_hz)                                                                                   \
  X(target_distance) X(speed) X(kp) X(heading_gain) X(max_yaw_rate) X(front_stop) X(scan_timeout) \
    X(sector_start) X(sector_end) X(fit_threshold)
#define INT_PARAMS(X) X(beam_stride) X(min_points)
using wall_follow::Parameters;
static std::string json(const Parameters & p)
{
  std::ostringstream s;
  s << std::setprecision(17) << "{";
#define FIELD(k) s << "\"" #k "\":" << p.k << ",";
  DOUBLE_PARAMS(FIELD)
  INT_PARAMS(FIELD)
#undef FIELD
    auto result = s.str();
  result.back() = '}';
  return result;
}
static double wall_now()
{
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static double cpu_now() { return double(std::clock()) / CLOCKS_PER_SEC; }
class Controller : public rclcpp::Node
{
public:
  Controller() : Node("wall_follower")
  {
#define DECLARE(k) p_.k = declare_parameter(#k, p_.k);
    DOUBLE_PARAMS(DECLARE)
    INT_PARAMS(DECLARE)
#undef DECLARE
      p_.validate();
    initial_ = p_;
    duration_ = declare_parameter("duration", 120.);
    if (!std::isfinite(duration_) || duration_ <= 0)
      throw std::invalid_argument("duration must be positive and finite");
    directory_ = declare_parameter("output_dir", std::string("results/run"));
    std::filesystem::create_directories(directory_);
    // Exclusive creation prevents accidentally replacing previous collected data.
    auto path = directory_ / "samples.csv";
    int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (fd < 0) throw std::runtime_error("Cannot create fresh samples.csv: " + path.string());
    ::close(fd);
    samples_.open(path);
    poses_.open(directory_ / "poses.csv");
    samples_.exceptions(std::ios::badbit | std::ios::failbit);
    poses_.exceptions(std::ios::badbit | std::ios::failbit);
    samples_ << std::setprecision(17)
             << "sim_time,elapsed,wall_elapsed,dt_sim,dt_wall,control_hz,target_distance,compute_"
                "ms,scan_age,state,estimated_distance,heading,pose_stamp,x,y,linear_cmd,angular_"
                "cmd,front_clearance,cpu_seconds,rss_kib\n";
    poses_ << std::setprecision(17) << "sim_time,stamp,x,y\n";
    save_metadata(false);
    pub_ = create_publisher<geometry_msgs::msg::Twist>("/cmd_vel", 10);
    scan_sub_ = create_subscription<sensor_msgs::msg::LaserScan>(
      "/scan", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::LaserScan::ConstSharedPtr s) { scan_ = s; });
    pose_sub_ = create_subscription<nav_msgs::msg::Odometry>(
      "/ground_truth", rclcpp::SensorDataQoS(), [this](nav_msgs::msg::Odometry::ConstSharedPtr m) {
        x_ = m->pose.pose.position.x;
        y_ = m->pose.pose.position.y;
        pose_stamp_ = rclcpp::Time(m->header.stamp).seconds();
        poses_ << now().seconds() << ',' << pose_stamp_ << ',' << x_ << ',' << y_ << '\n';
        poses_.flush();
      });
    reset_timer();
    callback_ =
      add_on_set_parameters_callback([this](const std::vector<rclcpp::Parameter> & values) {
        rcl_interfaces::msg::SetParametersResult result;
        auto candidate = p_;
        try {
          for (const auto & v : values) {
#define SET_DOUBLE(k)            \
  if (v.get_name() == #k) {      \
    candidate.k = v.as_double(); \
    continue;                    \
  }
#define SET_INT(k)            \
  if (v.get_name() == #k) {   \
    candidate.k = v.as_int(); \
    continue;                 \
  }
            DOUBLE_PARAMS(SET_DOUBLE)
            INT_PARAMS(SET_INT)
#undef SET_DOUBLE
#undef SET_INT
              throw std::invalid_argument("Only controller parameters are mutable during a run");
          }
          candidate.validate();
          bool changed = candidate.control_hz != p_.control_hz;
          p_ = candidate;
          if (changed) reset_timer();
          std::ostringstream event;
          event << std::setprecision(17) << "{\"sim_time\":" << now().seconds()
                << ",\"parameters\":" << json(p_) << "}";
          events_.push_back(event.str());
          save_metadata(false);
          result.successful = true;
        } catch (const std::exception & e) {
          result.successful = false;
          result.reason = e.what();
        }
        return result;
      });
  }
  bool finished() const { return finished_; }
  void finish(bool completed = false)
  {
    if (finished_) return;
    if (rclcpp::ok()) pub_->publish(geometry_msgs::msg::Twist());
    timer_->cancel();
    samples_.close();
    poses_.close();
    save_metadata(completed);
    finished_ = true;
  }

private:
  void reset_timer()
  {
    if (timer_) timer_->cancel();
    timer_ = rclcpp::create_timer(
      this, get_clock(), std::chrono::nanoseconds(static_cast<int64_t>(1e9 / p_.control_hz)),
      [this] { tick(); });
  }
  void save_metadata(bool completed)
  {
    struct utsname info
    {
    };
    uname(&info);
    std::ofstream f(directory_ / "metadata.json.tmp");
    f.exceptions(std::ios::badbit | std::ios::failbit);
    f << std::setprecision(17)
      << "{\"schema_version\":2,\"implementation\":\"cpp\",\"parameters\":" << json(initial_)
      << ",\"final_parameters\":" << json(p_) << ",\"duration\":" << duration_
      << ",\"completed\":" << (completed ? "true" : "false")
      << ",\"platform\":" << std::quoted(std::string(info.sysname) + " " + info.release)
      << ",\"machine\":" << std::quoted(info.machine)
      << ",\"compiler\":" << std::quoted(__VERSION__) << ",\"ros_distro\":"
      << std::quoted(std::getenv("ROS_DISTRO") ? std::getenv("ROS_DISTRO") : "")
      << ",\"source_sha256\":{\"controller.cpp\":\"" NODE_HASH "\",\"core.hpp\":\"" CORE_HASH
         "\"},\"parameter_events\":[";
    for (size_t i = 0; i < events_.size(); ++i) f << (i ? "," : "") << events_[i];
    f << "]}\n";
    f.close();
    std::filesystem::rename(directory_ / "metadata.json.tmp", directory_ / "metadata.json");
  }
  void tick()
  {
    double begin = wall_now(), sim = now().seconds();
    if (std::isnan(start_)) {
      start_ = sim;
      wall_start_ = begin;
      cpu_start_ = cpu_now();
    }
    double elapsed = sim - start_;
    if (elapsed >= duration_) {
      finish(true);
      return;
    }
    wall_follow::Command c;
    double age = wall_follow::nan;
    if (scan_) {
      age = sim - rclcpp::Time(scan_->header.stamp).seconds();
      if (age >= 0 && age <= p_.scan_timeout)
        c = wall_follow::command(
          scan_->ranges, scan_->angle_min, scan_->angle_increment, scan_->range_min,
          scan_->range_max, p_);
      else
        c.state = "stale_scan";
    }
    geometry_msgs::msg::Twist msg;
    msg.linear.x = c.speed;
    msg.angular.z = c.yaw;
    pub_->publish(msg);
    double compute = (wall_now() - begin) * 1000;
    struct rusage usage
    {
    };
    getrusage(RUSAGE_SELF, &usage);
    samples_ << sim << ',' << elapsed << ',' << begin - wall_start_ << ',' << sim - last_sim_ << ','
             << begin - last_wall_ << ',' << p_.control_hz << ',' << p_.target_distance << ','
             << compute << ',' << age << ',' << c.state << ',' << c.distance << ',' << c.heading
             << ',' << pose_stamp_ << ',' << x_ << ',' << y_ << ',' << c.speed << ',' << c.yaw
             << ',' << c.clearance << ',' << cpu_now() - cpu_start_ << ',' << usage.ru_maxrss
             << '\n';
    samples_.flush();
    last_sim_ = sim;
    last_wall_ = begin;
  }
  Parameters p_, initial_;
  double duration_, start_ = wall_follow::nan, wall_start_ = 0, cpu_start_ = 0,
                    last_sim_ = wall_follow::nan, last_wall_ = wall_follow::nan;
  double x_ = wall_follow::nan, y_ = wall_follow::nan, pose_stamp_ = wall_follow::nan;
  bool finished_ = false;
  std::filesystem::path directory_;
  std::ofstream samples_, poses_;
  std::vector<std::string> events_;
  sensor_msgs::msg::LaserScan::ConstSharedPtr scan_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr pub_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr pose_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
  OnSetParametersCallbackHandle::SharedPtr callback_;
};
int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  std::shared_ptr<Controller> node;
  int result = 0;
  try {
    node = std::make_shared<Controller>();
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(node);
    while (rclcpp::ok() && !node->finished()) executor.spin_once(std::chrono::milliseconds(100));
    node->finish();
  } catch (const std::exception & e) {
    std::cerr << e.what() << '\n';
    result = 1;
    if (node) {
      try {
        node->finish();
      } catch (...) {
      }
    }
  }
  rclcpp::shutdown();
  return result;
}
