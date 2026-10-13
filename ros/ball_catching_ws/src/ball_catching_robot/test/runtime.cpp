#include "ball_catching_robot/runtime.hpp"

#include <gtest/gtest.h>
#include <unistd.h>
#include <thread>

using namespace ball_catching;

TEST(Recording, GuestClockAndExportAreIndependentOfRosClock)
{
  rclcpp::init(0, nullptr);
  const auto directory = std::filesystem::path("/tmp") /
    ("chimaera-recording-test-" + std::to_string(getpid()));
  std::filesystem::create_directories(directory);
  const auto originFile = directory / "origin.ns";
  void * library = dlopen(FAKE_GEM5_CLOCK, RTLD_NOW | RTLD_LOCAL);
  ASSERT_NE(library, nullptr);
  const auto set = reinterpret_cast<void (*)(uint64_t)>(
    dlsym(library, "chimaera_test_set_time_ns"));
  ASSERT_NE(set, nullptr);
  rclcpp::NodeOptions options;
  options.parameter_overrides({rclcpp::Parameter("gem5_clock_library", FAKE_GEM5_CLOCK),
    rclcpp::Parameter("gem5_time_origin_file", originFile.string()),
    rclcpp::Parameter("use_sim_time", true), rclcpp::Parameter("record_telemetry", true)});
  auto node = std::make_shared<rclcpp::Node>("timing_test", options);
  std::optional<std::string> received;
  auto subscriber = node->create_subscription<std_msgs::msg::String>(
    "/benchmark/records", rclcpp::QoS(128).reliable(),
    [&](std_msgs::msg::String::ConstSharedPtr msg) { received = msg->data; });
  {
    Recorder record(directory.string(), "control_timing", node.get());
    ProcessingClock clock(*node);
    EXPECT_FALSE(clock.now());
    record.write({{"time", 0.}, {"processing_wall_seconds", nullptr}});
    EXPECT_EQ(std::filesystem::file_size(directory / "control_timing.jsonl"), 0);
    constexpr uint64_t origin = 5000000000;
    std::ofstream(originFile) << origin << '\n';
    set(origin + 1000000);
    const auto start = clock.now();
    set(origin + 3000000);
    EXPECT_EQ(node->now().nanoseconds(), 0);  // No /clock publisher.
    EXPECT_NEAR(std::get<double>(clock.elapsed(start)), .002, 1e-12);
    record.write({{"time", 0.}, {"processing_wall_seconds", clock.elapsed(start)}});
    const auto deadline = WallClock::now() + std::chrono::seconds(2);
    while (!received && WallClock::now() < deadline) {
      rclcpp::spin_some(node);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    ASSERT_TRUE(received);
    auto * envelope = json_tokener_parse(received->c_str());
    ASSERT_NE(envelope, nullptr);
    EXPECT_STREQ(json_object_get_string(json_object_object_get(envelope, "stream")), "control_timing");
    EXPECT_EQ(json_object_get_int64(json_object_object_get(envelope, "sequence")), 0);
    json_object_put(envelope);
  }
  auto * json = json_tokener_parse(readFile((directory / "control_timing.jsonl").string()).c_str());
  ASSERT_NE(json, nullptr);
  json_object * value;
  EXPECT_FALSE(json_object_object_get_ex(json, "processing_wall_seconds", &value));
  ASSERT_TRUE(json_object_object_get_ex(json, "processing_simulation_seconds", &value));
  EXPECT_DOUBLE_EQ(json_object_get_double(value), .002);
  ASSERT_TRUE(json_object_object_get_ex(json, "completed_time", &value));
  EXPECT_DOUBLE_EQ(json_object_get_double(value), .003);
  ASSERT_TRUE(json_object_object_get_ex(json, "timing_clock", &value));
  EXPECT_STREQ(json_object_get_string(value), "gem5 simulation");
  json_object_put(json);
  node.reset();
  subscriber.reset();
  dlclose(library);
  std::filesystem::remove_all(directory);
  rclcpp::shutdown();
}
