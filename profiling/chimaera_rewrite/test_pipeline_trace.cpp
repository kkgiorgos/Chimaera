#include "pipeline_trace.hpp"

#include <cassert>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

int main() {
  setenv("CHIMAERA_PIPELINE", "1", 1);
  std::string output;
  pipeline::initialize([] { return 100; },
    [&](const auto&, const auto& value) { output = value; });
  std::vector<unsigned char> bytes(1024);
  const auto put = [&](size_t at, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes[at + i] = value >> (8 * i);
  };
  put(64, 7);
  put(68, 123);
  put(72, 12);
  std::memcpy(bytes.data() + 76, "left_camera", 12);
  pipeline::image("wrapped_left", bytes.data(), bytes.size());
  put(72, 13);
  std::memcpy(bytes.data() + 76, "right_camera", 13);
  pipeline::image("wrapped_right", bytes.data(), bytes.size());
  put(68, 1000000000);
  pipeline::image("invalid_nanoseconds", bytes.data(), bytes.size());
  put(68, 123);
  put(72, 99);
  pipeline::image("invalid_string_size", bytes.data(), bytes.size());
  pipeline::flush();
  assert(output.find(",wrapped_left,1,7000000123,1024,100,") != std::string::npos);
  assert(output.find(",wrapped_right,2,7000000123,1024,100,") != std::string::npos);
  assert(output.find("invalid_") == std::string::npos);
}
