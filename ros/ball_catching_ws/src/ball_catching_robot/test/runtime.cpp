#include "ball_catching_robot/runtime.hpp"

#include <gtest/gtest.h>
#include <unistd.h>
#include <thread>

using namespace ball_catching;

TEST(Scheduling, GuestTimersAndStampsDoNotRequireClockMessages)
{
  rclcpp::init(0, nullptr);
  const auto directory = std::filesystem::path("/tmp") /
    ("chimaera-scheduling-test-" + std::to_string(getpid()));
  std::filesystem::create_directories(directory);
  const auto originFile = directory / "origin.ns";
  void * library = dlopen(FAKE_GEM5_CLOCK, RTLD_NOW | RTLD_LOCAL);
  ASSERT_NE(library, nullptr);
  const auto set = reinterpret_cast<void (*)(uint64_t)>(
    dlsym(library, "chimaera_test_set_time_ns"));
  ASSERT_NE(set, nullptr);
  auto node = std::make_shared<rclcpp::Node>("scheduling_test",
    rclcpp::NodeOptions().parameter_overrides({
      rclcpp::Parameter("gem5_clock_library", FAKE_GEM5_CLOCK),
      rclcpp::Parameter("gem5_time_origin_file", originFile.string()),
      rclcpp::Parameter("use_sim_time", false)}));
  ApplicationClock clock(*node);
  unsigned calls = 0;
  auto scheduled = timer(*node, .004, [&] { ++calls; });
  auto pump = [&] {
    for (int i = 0; i < 5; ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
      rclcpp::spin_some(node);
    }
  };
  EXPECT_FALSE(clock.now());
  pump();
  EXPECT_EQ(calls, 0u);  // No work before the exact time origin exists.
  constexpr uint64_t origin = 5000000000;
  std::ofstream(originFile) << origin << '\n';
  set(origin + 4000000);
  pump();
  ASSERT_TRUE(clock.now());
  EXPECT_EQ(clock.now()->nanoseconds(), 4000000);
  EXPECT_EQ(calls, 1u);  // Wall time alone cannot fire another callback.
  set(origin + 7999999);
  pump();
  EXPECT_EQ(calls, 1u);
  set(origin + 8000000);
  pump();
  EXPECT_EQ(calls, 2u);
  set(origin + 100000000);
  pump();
  EXPECT_EQ(calls, 3u);  // Skip missed periods instead of replaying stale work.
  EXPECT_EQ(node->count_publishers("/clock"), 0u);
  EXPECT_EQ(node->count_subscribers("/clock"), 0u);
  scheduled.reset();
  node.reset();
  std::filesystem::remove_all(directory);
  dlclose(library);
  rclcpp::shutdown();
}

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
    "/benchmark/control_timing", rclcpp::QoS(128).reliable(),
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

TEST(Recording, OnePumpServicesEveryPendingTimingStream)
{
  rclcpp::init(0, nullptr);
  auto node = std::make_shared<rclcpp::Node>("timing_stream_test",
    rclcpp::NodeOptions().parameter_overrides({rclcpp::Parameter("record_telemetry", true)}));
  std::array<unsigned, 4> received{};
  std::vector<rclcpp::Subscription<std_msgs::msg::String>::SharedPtr> subscribers;
  std::vector<std::unique_ptr<Recorder>> recorders;
  const std::array<std::string, 4> names{"perception", "interception", "control", "control_timing"};
  for (size_t i = 0; i < names.size(); ++i) {
    subscribers.push_back(node->create_subscription<std_msgs::msg::String>(
      "/benchmark/" + names[i], rclcpp::QoS(128).reliable(),
      [&, i](std_msgs::msg::String::ConstSharedPtr) { ++received[i]; }));
    recorders.push_back(std::make_unique<Recorder>("", names[i], node.get()));
  }
  // A backlog in one stream must not consume every callback slot and leave
  // all other streams behind the same FIFO. Match the bridge's bounded pump.
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  for (int sample = 0; sample < 10; ++sample)
    for (auto & recorder : recorders) recorder->write({{"time", double(sample)}});
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  executor.spin_some();
  for (auto count : received) EXPECT_EQ(count, 1);
  executor.remove_node(node);
  recorders.clear();
  subscribers.clear();
  node.reset();
  rclcpp::shutdown();
}
