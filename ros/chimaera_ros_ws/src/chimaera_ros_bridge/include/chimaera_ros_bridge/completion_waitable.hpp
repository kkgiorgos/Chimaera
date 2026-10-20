#pragma once

#include <rclcpp/guard_condition.hpp>
#include <rclcpp/waitable.hpp>
#include <stdexcept>

namespace chimaera_ros_bridge
{
// Integrate the driver's interrupt completion thread into the DDS wait set.
// A triggered guard remains pending when notification precedes executor wait.
// All application callbacks and transport servicing stay on the executor thread.
class CompletionWaitable final : public rclcpp::Waitable
{
public:
  void notify() { guard_.trigger(); }
  std::size_t get_number_of_ready_guard_conditions() override { return 1; }
  void add_to_wait_set(rcl_wait_set_t * wait_set) override
  {
    if (
      rcl_wait_set_add_guard_condition(wait_set, &guard_.get_rcl_guard_condition(), &index_) !=
      RCL_RET_OK)
      throw std::runtime_error("Cannot add DMA completion guard to ROS wait set");
  }
  bool is_ready(rcl_wait_set_t * wait_set) override
  {
    return index_ < wait_set->size_of_guard_conditions && wait_set->guard_conditions[index_];
  }
  std::shared_ptr<void> take_data() override { return {}; }
  void execute(std::shared_ptr<void> &) override {}

private:
  rclcpp::GuardCondition guard_;
  std::size_t index_{};
};
}  // namespace chimaera_ros_bridge
