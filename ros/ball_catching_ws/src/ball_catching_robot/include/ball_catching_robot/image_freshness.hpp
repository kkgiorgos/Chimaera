#pragma once

#include <dlfcn.h>

#include <algorithm>
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

// Reject queued work that is fresh on entry but cannot plausibly finish while
// fresh. The estimate is measured on the same simulation clock as the deadline.
// A capped, decaying high-water mark recovers after a scheduling outlier.
// Skipping otherwise fresh pairs also decays it, so a pessimistic estimate
// cannot permanently block work when transport latency exceeds that window.
class ImageAdmission
{
public:
  explicit ImageAdmission(int64_t budget) : budget_(budget)
  {
    if (budget <= 0) throw std::invalid_argument("image budget must be positive");
  }
  bool accepts(int64_t capture, std::optional<int64_t> now)
  {
    if (!fresh_image(capture, now, budget_)) return false;
    if (fresh_image(capture, now, budget_ - estimate_)) return true;
    estimate_ -= std::max(int64_t{1}, estimate_ / 8);
    return false;
  }
  void observe(std::optional<int64_t> start, std::optional<int64_t> end)
  {
    if (!start || !end || *start < 0 || *end < *start) return;
    const auto duration = std::min(*end - *start, budget_ - std::max(int64_t{1}, budget_ / 10));
    estimate_ = std::max(duration, estimate_ - std::max(int64_t{1}, estimate_ / 8));
  }
  int64_t estimate() const { return estimate_; }

private:
  int64_t budget_, estimate_{};
};
}  // namespace ball_catching
