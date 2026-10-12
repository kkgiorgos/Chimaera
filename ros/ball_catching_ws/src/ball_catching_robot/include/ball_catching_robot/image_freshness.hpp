#pragma once

#include <dlfcn.h>

#include <cstdint>
#include <fstream>
#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

namespace ball_catching
{
class ImageClock
{
public:
  ImageClock(
    std::function<int64_t()> native_clock, const std::string & library = "",
    const std::string & origin_file = "")
  : native_(std::move(native_clock)), origin_file_(origin_file)
  {
    if (library.empty() != origin_file.empty())
      throw std::invalid_argument("clock library and origin file must be supplied together");
    if (library.empty()) return;
    library_ = dlopen(library.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!library_)
      throw std::runtime_error(std::string("cannot load simulation clock: ") + dlerror());
    read_ = reinterpret_cast<uint64_t (*)()>(dlsym(library_, "chimaera_gem5_time_ns"));
    if (!read_) {
      dlclose(library_);
      library_ = nullptr;
      throw std::runtime_error("simulation clock symbol missing");
    }
  }
  ~ImageClock()
  {
    if (library_) dlclose(library_);
  }
  ImageClock(const ImageClock &) = delete;
  ImageClock & operator=(const ImageClock &) = delete;

  std::optional<int64_t> now()
  {
    if (!library_) return native_();
    if (!origin_) {
      std::ifstream input(origin_file_);
      if (!input) return std::nullopt;
      uint64_t origin;
      std::string extra;
      if (!(input >> origin) || (input >> extra))
        throw std::runtime_error("invalid simulation clock origin");
      origin_ = origin;
    }
    const auto current = read_();
    if (current < *origin_ || current - *origin_ > INT64_MAX) return std::nullopt;
    return static_cast<int64_t>(current - *origin_);
  }

private:
  std::function<int64_t()> native_;
  std::string origin_file_;
  void * library_{};
  uint64_t (*read_)(){};
  std::optional<uint64_t> origin_;
};

inline bool fresh_image(int64_t capture, std::optional<int64_t> now, int64_t budget)
{
  return now && capture >= 0 && *now >= capture && *now - capture <= budget;
}
}  // namespace ball_catching
