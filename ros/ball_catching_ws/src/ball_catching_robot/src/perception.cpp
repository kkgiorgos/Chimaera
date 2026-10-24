#include <array>
#include <cstdio>
#include <geometry_msgs/msg/vector3_stamped.hpp>
#include <map>
#include <nav_msgs/msg/odometry.hpp>
#include <opencv2/imgproc.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/bool.hpp>

#include "ball_catching_robot/image_freshness.hpp"
#include "ball_catching_robot/runtime.hpp"

namespace ball_catching
{
class Perception : public rclcpp::Node
{
public:
  Perception()
  : Node("ball_perception"),
    stereo_(
      declare_parameter("focal", 400.), declare_parameter("cx", 320.),
      declare_parameter("cy", 240.), declare_parameter("baseline", .3), cameraOrigin()),
    record_(declare_parameter<std::string>("output", ""), "perception", this),
    processingClock_(*this),
    imageClock_(
      [this] { return now().nanoseconds(); }, get_parameter("gem5_clock_library").as_string(),
      get_parameter("gem5_time_origin_file").as_string())
  {
    const auto cameraHz = declare_parameter("camera_hz", 90.);
    const auto periods = declare_parameter("image_age_periods", 2.);
    if (
      !std::isfinite(cameraHz) || !std::isfinite(periods) || cameraHz <= 0 || periods <= 0 ||
      periods / cameraHz > 3600)
      throw std::invalid_argument("invalid image freshness budget");
    imageAgeNs_ = static_cast<int64_t>(std::ceil(periods / cameraHz * 1e9));
    admission_ = std::make_unique<ImageAdmission>(imageAgeNs_);
    imageDiagnostics_ = declare_parameter<bool>("image_diagnostics", false);
    cv::setNumThreads(1);
    state_ =
      create_publisher<nav_msgs::msg::Odometry>("/robot/ball_state", rclcpp::SensorDataQoS());
    geometry_ = create_publisher<geometry_msgs::msg::Vector3Stamped>(
      "/robot/ball_geometry", rclcpp::SensorDataQoS());
    ready_ = create_publisher<std_msgs::msg::Bool>("/robot/perception_ready", 1);
    timer_ = timer(*this, std::min(.1, periods / cameraHz / 2), [this] {
      std_msgs::msg::Bool msg;
      msg.data = fresh_image(lastCapture_, imageClock_.now(), imageAgeNs_);
      ready_->publish(msg);
    });
    for (int i = 0; i < 2; ++i) {
      const std::string side = i == 0 ? "left" : "right";
      subscriptions_[i] = create_subscription<sensor_msgs::msg::Image>(
        "/stereo/" + side + "/image_raw", rclcpp::SensorDataQoS().keep_last(1),
        [this, i](sensor_msgs::msg::Image::ConstSharedPtr msg) { image(msg, i); });
    }
  }
  ~Perception() override
  {
    RCLCPP_INFO(
      get_logger(), "Image freshness: stale inputs=%lu, budget drops=%lu, expired processing=%lu",
      staleInputs_, budgetDrops_, expiredPairs_);
  }

private:
  Vec3 cameraOrigin()
  {
    const auto origin = declare_parameter<std::vector<double>>("camera_origin", {-.65, .15, 1.});
    if (origin.size() != 3) throw std::invalid_argument("camera_origin needs three coordinates");
    return Eigen::Map<const Vec3>(origin.data());
  }
  void image(sensor_msgs::msg::Image::ConstSharedPtr msg, int side)
  {
    const int64_t key = rclcpp::Time(msg->header.stamp).nanoseconds();
    if (key <= lastPair_) return;
    if (!fresh_image(key, imageTime("input", key), imageAgeNs_)) {
      staleInput();
      return;
    }
    buffers_[side][key] = msg;
    for (auto & buffer : buffers_)
      while (buffer.size() > 3) buffer.erase(buffer.begin());
    if (!buffers_[1 - side].count(key)) return;
    lastPair_ = key;
    const std::array<sensor_msgs::msg::Image::ConstSharedPtr, 2> pair{
      buffers_[0].at(key), buffers_[1].at(key)};
    for (auto & buffer : buffers_) buffer.erase(buffer.begin(), buffer.upper_bound(key));
    const auto current = imageTime("paired", key);
    if (!fresh_image(key, current, imageAgeNs_)) {
      staleInput();
      return;
    }
    if (!admission_->accepts(key, current)) {
      if (++budgetDrops_ == 1)
        RCLCPP_WARN(get_logger(), "Skipping queued stereo pairs without processing headroom");
      return;
    }
    lastImages_ = static_cast<double>(*current) * 1e-9;
    const auto started = processingClock_.now();
    const auto recordExpired =
      [&](Field conversion, Field detection = nullptr, Field estimation = nullptr) {
        record_.write(
          {{"capture_time", seconds(msg->header.stamp)},
           {"receive_time", lastImages_},
           {"status", "expired_processing"},
           {"processing_wall_seconds", processingClock_.elapsed(started)},
           {"image_conversion_wall_seconds", conversion},
           {"detection_wall_seconds", detection},
           {"estimation_wall_seconds", estimation},
           {"budget_drops", static_cast<double>(budgetDrops_)},
           {"stale_inputs", static_cast<double>(staleInputs_)}});
      };
    std::array<cv::Mat, 2> images;
    for (int i = 0; i < 2; ++i) {
      const auto & frame = *pair[i];
      if (
        (frame.encoding != "rgb8" && frame.encoding != "bgr8") || frame.step < frame.width * 3 ||
        frame.data.size() < static_cast<size_t>(frame.height) * frame.step || frame.height == 0 ||
        frame.width == 0) {
        RCLCPP_ERROR(get_logger(), "Expected valid RGB8/BGR8 stereo images");
        return;
      }
      const cv::Mat view(
        frame.height, frame.width, CV_8UC3, const_cast<uint8_t *>(frame.data.data()), frame.step);
      if (frame.encoding == "bgr8")
        cv::cvtColor(view, images[i], cv::COLOR_BGR2RGB);
      else
        images[i] = view;
    }
    const auto conversionSeconds = processingClock_.elapsed(started);
    const auto converted = imageTime("converted", key);
    if (!fresh_image(key, converted, imageAgeNs_)) {
      admission_->observe(current, converted);
      expiredPair();
      recordExpired(conversionSeconds);
      return;
    }
    const auto detectionStarted = processingClock_.now();
    const double capture = seconds(msg->header.stamp);
    const auto detection = stereo_.detect(images[0], images[1], filter_.predict(capture));
    const auto detectionSeconds = processingClock_.elapsed(detectionStarted);
    const auto detected = imageTime("detected", key);
    admission_->observe(current, detected);
    if (!fresh_image(key, detected, imageAgeNs_)) {
      expiredPair();
      recordExpired(conversionSeconds, detectionSeconds);
      return;
    }
    if (!detection) {
      lastCapture_ = key;
      record_.write(
        {{"capture_time", capture},
         {"receive_time", lastImages_},
         {"processing_wall_seconds", processingClock_.elapsed(started)},
         {"image_conversion_wall_seconds", conversionSeconds},
         {"detection_wall_seconds", detectionSeconds},
         {"budget_drops", static_cast<double>(budgetDrops_)},
         {"stale_inputs", static_cast<double>(staleInputs_)},
         {"status", "no_detection"}});
      return;
    }
    const auto estimationStarted = processingClock_.now();
    auto candidate = filter_;
    const auto estimate =
      candidate.observe(detection->point, capture, stereo_.observationNoise(detection->point));
    const auto estimationSeconds = processingClock_.elapsed(estimationStarted);
    const auto publicationStarted = processingClock_.now();
    const auto estimated = imageTime("estimated", key);
    admission_->observe(current, estimated);
    if (!fresh_image(key, estimated, imageAgeNs_)) {
      expiredPair();
      recordExpired(conversionSeconds, detectionSeconds, estimationSeconds);
      return;
    }
    lastCapture_ = key;
    filter_ = std::move(candidate);
    geometry_msgs::msg::Vector3Stamped geometry;
    geometry.header.stamp = msg->header.stamp;
    geometry.header.frame_id = "ball";
    geometry.vector.x = detection->radius;
    geometry_->publish(geometry);
    if (estimate) {
      nav_msgs::msg::Odometry state;
      state.header.stamp = msg->header.stamp;
      state.header.frame_id = "world";
      state.child_frame_id = "ball";
      auto & p = state.pose.pose.position;
      p.x = (*estimate)[0];
      p.y = (*estimate)[1];
      p.z = (*estimate)[2];
      state.pose.pose.orientation.w = 1;
      auto & v = state.twist.twist.linear;
      v.x = (*estimate)[3];
      v.y = (*estimate)[4];
      v.z = (*estimate)[5];
      const Mat6 covariance = filter_.covariance();
      for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) {
          state.pose.covariance[i * 6 + j] = covariance(i, j);
          state.twist.covariance[i * 6 + j] = covariance(i + 3, j + 3);
        }
      state_->publish(state);
    }
    const auto publicationSeconds = processingClock_.elapsed(publicationStarted);
    if (imageDiagnostics_) imageTime("published", key);
    record_.write(
      {{"capture_time", capture},
       {"receive_time", lastImages_},
       {"processing_wall_seconds", processingClock_.elapsed(started)},
       {"image_conversion_wall_seconds", conversionSeconds},
       {"detection_wall_seconds", detectionSeconds},
       {"estimation_wall_seconds", estimationSeconds},
       {"publication_wall_seconds", publicationSeconds},
       {"budget_drops", static_cast<double>(budgetDrops_)},
       {"stale_inputs", static_cast<double>(staleInputs_)},
       {"observation", values(detection->point)},
       {"radius", detection->radius},
       {"state", estimate ? Field(values(*estimate)) : Field(nullptr)}});
  }
  Stereo stereo_;
  std::optional<int64_t> imageTime(const char * stage, int64_t capture)
  {
    const auto time = imageClock_.now();
    if (imageDiagnostics_ && time) {
      auto & last = imageTrace_[stage];
      if (last.first++ < 3 || *time - last.second >= 100000000) {
        last.second = *time;
        std::fprintf(
          stderr,
          "CHIMAERA_IMAGE component=perception stage=%s capture_ns=%ld now_ns=%ld age_ns=%ld "
          "budget_ns=%ld\n",
          stage, capture, *time, *time - capture, imageAgeNs_);
      }
    }
    return time;
  }
  void staleInput()
  {
    if (++staleInputs_ == 1) RCLCPP_WARN(get_logger(), "Rejecting stale images before perception");
  }
  void expiredPair()
  {
    if (++expiredPairs_ == 1)
      RCLCPP_WARN(get_logger(), "Discarding detections that expired during processing");
  }
  BallFilter filter_;
  Recorder record_;
  ProcessingClock processingClock_;
  ImageClock imageClock_;
  int64_t imageAgeNs_{}, lastCapture_{-1};
  uint64_t staleInputs_{}, budgetDrops_{}, expiredPairs_{};
  std::unique_ptr<ImageAdmission> admission_;
  bool imageDiagnostics_{};
  std::map<std::string, std::pair<uint64_t, int64_t>> imageTrace_;
  int64_t lastPair_{-1};
  double lastImages_{-1};
  std::array<std::map<int64_t, sensor_msgs::msg::Image::ConstSharedPtr>, 2> buffers_;
  std::array<rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr, 2> subscriptions_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr state_;
  rclcpp::Publisher<geometry_msgs::msg::Vector3Stamped>::SharedPtr geometry_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr ready_;
  rclcpp::TimerBase::SharedPtr timer_;
};
std::shared_ptr<rclcpp::Node> make_perception() { return std::make_shared<Perception>(); }
}  // namespace ball_catching
#ifndef BALL_CATCHING_COMPOSED
int main(int argc, char ** argv)
{
  return ball_catching::run<ball_catching::Perception>(argc, argv);
}
#endif
