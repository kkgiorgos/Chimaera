#include "ball_catching_robot/runtime.hpp"
#include <geometry_msgs/msg/vector3_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/bool.hpp>
#include <opencv2/imgproc.hpp>
#include <array>
#include <map>

namespace ball_catching {
class Perception : public rclcpp::Node {
 public:
  Perception() : Node("ball_perception"),
      stereo_(declare_parameter("focal", 400.), declare_parameter("cx", 320.),
              declare_parameter("cy", 240.), declare_parameter("baseline", .3), cameraOrigin()),
      record_(declare_parameter<std::string>("output", ""), "perception") {
    cv::setNumThreads(1);
    state_ = create_publisher<nav_msgs::msg::Odometry>("/robot/ball_state", rclcpp::SensorDataQoS());
    geometry_ = create_publisher<geometry_msgs::msg::Vector3Stamped>("/robot/ball_geometry", rclcpp::SensorDataQoS());
    ready_ = create_publisher<std_msgs::msg::Bool>("/robot/perception_ready", 1);
    timer_ = timer(*this, .1, [this] {
      const double age = now().seconds() - lastImages_;
      std_msgs::msg::Bool msg; msg.data = age >= 0 && age < .2; ready_->publish(msg);
    });
    for (int i = 0; i < 2; ++i) {
      const std::string side = i == 0 ? "left" : "right";
      subscriptions_[i] = create_subscription<sensor_msgs::msg::Image>("/stereo/" + side + "/image_raw",
          rclcpp::SensorDataQoS(), [this, i](sensor_msgs::msg::Image::ConstSharedPtr msg) { image(msg, i); });
    }
  }
 private:
  Vec3 cameraOrigin() {
    const auto origin = declare_parameter<std::vector<double>>("camera_origin", {-.65, .15, 1.});
    if (origin.size() != 3) throw std::invalid_argument("camera_origin needs three coordinates");
    return Eigen::Map<const Vec3>(origin.data());
  }
  void image(sensor_msgs::msg::Image::ConstSharedPtr msg, int side) {
    const int64_t key = rclcpp::Time(msg->header.stamp).nanoseconds();
    if (key <= lastPair_) return;
    buffers_[side][key] = msg;
    for (auto &buffer : buffers_) while (buffer.size() > 3) buffer.erase(buffer.begin());
    if (!buffers_[1 - side].count(key)) return;
    lastPair_ = key;
    const std::array<sensor_msgs::msg::Image::ConstSharedPtr, 2> pair{buffers_[0].at(key), buffers_[1].at(key)};
    for (auto &buffer : buffers_) buffer.erase(key);
    lastImages_ = now().seconds();
    const auto started = WallClock::now();
    std::array<cv::Mat, 2> images;
    for (int i = 0; i < 2; ++i) {
      const auto &frame = *pair[i];
      if ((frame.encoding != "rgb8" && frame.encoding != "bgr8") || frame.step < frame.width*3 ||
          frame.data.size() < static_cast<size_t>(frame.height)*frame.step || frame.height == 0 || frame.width == 0) {
        RCLCPP_ERROR(get_logger(), "Expected valid RGB8/BGR8 stereo images"); return;
      }
      const cv::Mat view(frame.height, frame.width, CV_8UC3, const_cast<uint8_t *>(frame.data.data()), frame.step);
      if (frame.encoding == "bgr8") cv::cvtColor(view, images[i], cv::COLOR_BGR2RGB);
      else images[i] = view;
    }
    const double capture = seconds(msg->header.stamp);
    const auto detection = stereo_.detect(images[0], images[1], filter_.predict(capture));
    if (!detection) return;
    geometry_msgs::msg::Vector3Stamped geometry;
    geometry.header.stamp = msg->header.stamp; geometry.header.frame_id = "ball";
    geometry.vector.x = detection->radius;
    geometry_->publish(geometry);
    const auto estimate = filter_.observe(detection->point, capture);
    record_.write({{"capture_time", capture}, {"receive_time", lastImages_},
                   {"processing_wall_seconds", elapsed(started)}, {"observation", values(detection->point)},
                   {"radius", detection->radius}, {"state", estimate ? Field(values(*estimate)) : Field(nullptr)}});
    if (!estimate) return;
    nav_msgs::msg::Odometry state;
    state.header.stamp = msg->header.stamp; state.header.frame_id = "world"; state.child_frame_id = "ball";
    auto &p = state.pose.pose.position; p.x = (*estimate)[0]; p.y = (*estimate)[1]; p.z = (*estimate)[2];
    state.pose.pose.orientation.w = 1;
    auto &v = state.twist.twist.linear; v.x = (*estimate)[3]; v.y = (*estimate)[4]; v.z = (*estimate)[5];
    for (int i = 0; i < 3; ++i) for (int j = 0; j < 3; ++j) {
      state.pose.covariance[i*6 + j] = filter_.covariance()(i, j);
      state.twist.covariance[i*6 + j] = filter_.covariance()(i + 3, j + 3);
    }
    state_->publish(state);
  }
  Stereo stereo_;
  BallFilter filter_;
  Recorder record_;
  int64_t lastPair_{-1};
  double lastImages_{-1};
  std::array<std::map<int64_t, sensor_msgs::msg::Image::ConstSharedPtr>, 2> buffers_;
  std::array<rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr, 2> subscriptions_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr state_;
  rclcpp::Publisher<geometry_msgs::msg::Vector3Stamped>::SharedPtr geometry_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr ready_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}
int main(int argc, char **argv) { return ball_catching::run<ball_catching::Perception>(argc, argv); }
