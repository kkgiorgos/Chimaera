#include <mock_sim/mock_controller.hpp>
#include <talker_listener_bridge/bridge.hpp>
#include <iostream>

// Keep the production bridge unchanged; adapt the identical application handler
// interfaces to mock-sim's separate namespace for this integration test.
struct Adapter : mock_sim::DataProducer, mock_sim::DataConsumer
{
  explicit Adapter(talker_listener_bridge::Bridge & bridge) : bridge(bridge) {}
  std::optional<mock_sim::Message> take() override {return bridge.take();}
  void submit(mock_sim::Message message) override {bridge.submit(std::move(message));}
  talker_listener_bridge::Bridge & bridge;
};
int main(int argc, char ** argv)
{
  if (argc < 3) {return 2;}
  const std::string side = argv[1], endpoint = argv[2];
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto bridge = std::make_shared<talker_listener_bridge::Bridge>(side);
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(bridge);
    bridge->pump = [&] {executor.spin_some();};
    Adapter adapter(*bridge);
    if (side == "host") {
      mock_sim::MockTimingController timing;
      mock_sim::MockHostController controller(endpoint, timing, adapter, adapter);
      for (int i = 0; i < 100; ++i) {
        const auto result = controller.step(std::chrono::milliseconds(100), std::chrono::milliseconds(10));
        if (!result.ok()) {throw std::runtime_error(result.message);}
      }
      const auto result = controller.stop();
      if (result.state == mock_sim::ControllerState::failed) {throw std::runtime_error(result.message);}
    } else {
      mock_sim::MockGuestController controller(endpoint, adapter, adapter);
      while (true) {
        const auto result = controller.run_next();
        if (result.state == mock_sim::ControllerState::stopped) {break;}
        if (!result.ok()) {throw std::runtime_error(result.message);}
      }
    }
    if (!bridge->received() || !bridge->transmitted()) {
      throw std::runtime_error("expected traffic in both directions");
    }
    std::cout << side << " mock bridge exchanged traffic\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n'; status = 1;
  }
  rclcpp::shutdown();
  return status;
}
