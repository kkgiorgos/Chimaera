#include <chimaera_ros_bridge/serialized_view.hpp>
#include <stdexcept>
#include <vector>

int main()
{
  std::vector<std::byte> image(640 * 480 * 3, std::byte{0xa5});
  try {
    chimaera_ros_bridge::SerializedView view(image.data(), image.size());
    const auto & raw = view.message().get_rcl_serialized_message();
    if (
      raw.buffer != reinterpret_cast<const uint8_t *>(image.data()) ||
      raw.buffer_length != image.size() || raw.buffer_capacity != image.size()) {
      return 1;
    }
    // Publish failures must also restore the original owning handle.
    throw std::runtime_error("simulated synchronous publish failure");
  } catch (const std::runtime_error &) {
  }
  for (const auto value : image) {
    if (value != std::byte{0xa5}) return 1;
  }
  image.clear();
  image.shrink_to_fit();  // Detect double-free/use-after-free under ASan.
  return 0;
}
