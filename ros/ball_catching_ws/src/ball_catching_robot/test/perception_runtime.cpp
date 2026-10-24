#include <gtest/gtest.h>
#include <unistd.h>

#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/bool.hpp>
#include <thread>

#include "ball_catching_robot/components.hpp"
#include "ball_catching_robot/runtime.hpp"

using namespace ball_catching;

TEST(Perception, SlowExecutorConsumesOnlyLatestStereoAndRequiresCompletedFreshWork)
{
  const auto directory =
    std::filesystem::path("/tmp") / ("chimaera-perception-test-" + std::to_string(getpid()));
  std::filesystem::create_directories(directory);
  const auto originFile = directory / "origin.ns";
  constexpr uint64_t origin = 5000000000;
  std::ofstream(originFile) << origin << '\n';
  void * library = dlopen(FAKE_GEM5_CLOCK, RTLD_NOW | RTLD_LOCAL);
  ASSERT_NE(library, nullptr);
  const auto set =
    reinterpret_cast<void (*)(uint64_t)>(dlsym(library, "chimaera_test_set_time_ns"));
  ASSERT_NE(set, nullptr);
  set(origin + 200000000);
  const std::vector<std::string> arguments{
    "perception_test",
    "--ros-args",
    "-p",
    "output:=" + directory.string(),
    "-p",
    "gem5_clock_library:=" FAKE_GEM5_CLOCK,
    "-p",
    "gem5_time_origin_file:=" + originFile.string(),
    "-p",
    "camera_hz:=30.0",
    "-p",
    "use_sim_time:=false",
    "-p",
    "record_telemetry:=true"};
  std::vector<const char *> argv;
  for (const auto & argument : arguments) argv.push_back(argument.c_str());
  rclcpp::init(static_cast<int>(argv.size()), argv.data());
  struct Shutdown
  {
    ~Shutdown() { rclcpp::shutdown(); }
  } shutdown;
  auto perception = make_perception();
  auto producer = std::make_shared<rclcpp::Node>("perception_input_test");
  auto left = producer->create_publisher<sensor_msgs::msg::Image>(
    "/stereo/left/image_raw", rclcpp::SensorDataQoS());
  auto right = producer->create_publisher<sensor_msgs::msg::Image>(
    "/stereo/right/image_raw", rclcpp::SensorDataQoS());
  std::optional<bool> ready;
  auto readiness = producer->create_subscription<std_msgs::msg::Bool>(
    "/robot/perception_ready", 1,
    [&](std_msgs::msg::Bool::ConstSharedPtr message) { ready = message->data; });
  const auto deadline = WallClock::now() + std::chrono::seconds(3);
  while ((left->get_subscription_count() != 1 || right->get_subscription_count() != 1) &&
         WallClock::now() < deadline)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  ASSERT_EQ(left->get_subscription_count(), 1u);
  ASSERT_EQ(right->get_subscription_count(), 1u);

  // Deliberately leave the real perception executor unserviced while several
  // camera periods arrive. Its next callback must use the newest frame, rather
  // than spend another detection pass on the still-fresh 166 ms frame.
  sensor_msgs::msg::Image image;
  image.width = image.height = 64;
  image.step = 64 * 3;
  image.encoding = "rgb8";
  image.data.resize(image.height * image.step, 0);
  for (int capture : {100, 133, 166, 199}) {
    image.header.stamp = rclcpp::Time(capture * 1000000LL);
    left->publish(image);
    right->publish(image);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(perception);
  executor.add_node(producer);
  auto pump = [&] {
    for (int i = 0; i < 30; ++i) {
      executor.spin_some();
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
  };
  pump();
  const auto records = readFile((directory / "perception.jsonl").string());
  EXPECT_EQ(std::count(records.begin(), records.end(), '\n'), 1);
  auto * record = json_tokener_parse(records.c_str());
  ASSERT_NE(record, nullptr);
  EXPECT_DOUBLE_EQ(json_object_get_double(json_object_object_get(record, "capture_time")), .199);
  EXPECT_STREQ(json_object_get_string(json_object_object_get(record, "status")), "no_detection");
  json_object_put(record);
  set(origin + 240000000);
  pump();
  ASSERT_TRUE(ready);
  EXPECT_TRUE(*ready);
  set(origin + 300000000);
  pump();
  ASSERT_TRUE(ready);
  EXPECT_FALSE(*ready);  // Queued/old images cannot keep readiness alive.
  executor.remove_node(producer);
  executor.remove_node(perception);
  perception.reset();
  dlclose(library);
  std::filesystem::remove_all(directory);
}

TEST(Perception, NativeAdmissionMeasuresWorkOnTheImageClock)
{
  const auto directory =
    std::filesystem::path("/tmp") / ("chimaera-native-perception-test-" + std::to_string(getpid()));
  std::filesystem::create_directories(directory);
  const std::vector<std::string> arguments{
    "perception_test",
    "--ros-args",
    "-p",
    "output:=" + directory.string(),
    "-p",
    "camera_hz:=30.0",
    "-p",
    "image_age_periods:=6.0",
    "-p",
    "use_sim_time:=false"};
  std::vector<const char *> argv;
  for (const auto & argument : arguments) argv.push_back(argument.c_str());
  rclcpp::init(static_cast<int>(argv.size()), argv.data());
  struct Shutdown
  {
    ~Shutdown() { rclcpp::shutdown(); }
  } shutdown;
  auto perception = make_perception();
  auto producer = std::make_shared<rclcpp::Node>("native_perception_input_test");
  auto left = producer->create_publisher<sensor_msgs::msg::Image>(
    "/stereo/left/image_raw", rclcpp::SensorDataQoS());
  auto right = producer->create_publisher<sensor_msgs::msg::Image>(
    "/stereo/right/image_raw", rclcpp::SensorDataQoS());
  const auto deadline = WallClock::now() + std::chrono::seconds(3);
  while ((left->get_subscription_count() != 1 || right->get_subscription_count() != 1) &&
         WallClock::now() < deadline)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  ASSERT_EQ(left->get_subscription_count(), 1u);
  ASSERT_EQ(right->get_subscription_count(), 1u);
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(perception);
  sensor_msgs::msg::Image image;
  image.width = image.height = 64;
  image.step = 64 * 3;
  image.encoding = "rgb8";
  image.data.resize(image.height * image.step, 0);
  for (int expected = 1; expected <= 2; ++expected) {
    image.header.stamp = rclcpp::Time(producer->now().nanoseconds() - 50000000);
    left->publish(image);
    right->publish(image);
    const auto until = WallClock::now() + std::chrono::seconds(2);
    std::string records;
    do {
      executor.spin_some();
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
      records = readFile((directory / "perception.jsonl").string());
    } while (std::count(records.begin(), records.end(), '\n') < expected &&
             WallClock::now() < until);
    // Mixing steady processing time with the native ROS image clock would cap
    // the estimate at 180 ms and incorrectly reject the second 50 ms-old pair.
    EXPECT_EQ(std::count(records.begin(), records.end(), '\n'), expected);
    EXPECT_EQ(records.find("\"budget_drops\":1"), std::string::npos);
  }
  executor.remove_node(perception);
  perception.reset();
  std::filesystem::remove_all(directory);
}
