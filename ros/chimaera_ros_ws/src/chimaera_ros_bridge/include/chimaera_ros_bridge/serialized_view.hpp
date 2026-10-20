#pragma once

#include <cstddef>
#include <rclcpp/serialized_message.hpp>

namespace chimaera_ros_bridge
{
// A synchronous publish may borrow immutable bytes from the owning transport
// frame. Restore the original handle before destruction so rclcpp never frees
// the borrowed storage, including when publish throws. The frame must outlive
// this view and publish must complete before its storage is reused.
class SerializedView
{
public:
  SerializedView(const std::byte * bytes, std::size_t size)
  : original_(message_.get_rcl_serialized_message())
  {
    auto & handle = message_.get_rcl_serialized_message();
    handle.buffer = reinterpret_cast<uint8_t *>(const_cast<std::byte *>(bytes));
    handle.buffer_length = size;
    handle.buffer_capacity = size;
  }
  ~SerializedView() { message_.get_rcl_serialized_message() = original_; }
  SerializedView(const SerializedView &) = delete;
  SerializedView & operator=(const SerializedView &) = delete;
  const rclcpp::SerializedMessage & message() const { return message_; }

private:
  rclcpp::SerializedMessage message_;
  rcl_serialized_message_t original_;
};
}  // namespace chimaera_ros_bridge
