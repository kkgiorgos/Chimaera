#include "pipeline_trace.hpp"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace pipeline {
namespace {
struct Row { const char* stage; unsigned stream; uint64_t capture; size_t bytes; Stamp at; };
std::mutex mutex;
std::vector<Row> rows;
std::function<uint64_t()> clock_fn;
std::function<void(const std::string&, const std::string&)> write_fn;
bool finished = false;
uint64_t ns(clockid_t clock) {
  timespec value{};
  clock_gettime(clock, &value);
  return uint64_t(value.tv_sec) * 1000000000 + value.tv_nsec;
}
uint32_t le32(const unsigned char* p) {
  return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
}
void initialize(std::function<uint64_t()> clock,
                std::function<void(const std::string&, const std::string&)> writer) {
  std::lock_guard lock(mutex);
  if (!std::getenv("CHIMAERA_PIPELINE") || clock_fn || finished) return;
  rows.reserve(32768);
  clock_fn = std::move(clock);
  write_fn = std::move(writer);
}
Stamp stamp() {
  std::lock_guard lock(mutex);
  if (!clock_fn || finished) return {unset, unset, unset};
  const auto wall = ns(CLOCK_MONOTONIC);
  const auto cpu = ns(CLOCK_THREAD_CPUTIME_ID);
  return {clock_fn(), wall, cpu};
}
void event(const char* stage, unsigned stream, uint64_t capture, size_t bytes) {
  const auto at = stamp();
  if (at.sim == unset) return;
  std::lock_guard lock(mutex);
  if (finished) return;
  if (rows.size() >= 32768) throw std::runtime_error("pipeline trace overflow");
  rows.push_back({stage, stream, capture, bytes, at});
}
void image(const char* stage, const void* data, size_t size, Stamp at) {
  // Locate the benchmark's CDR Header, including inside RTPS and CDM2 frames.
  // Require its exact frame string and a plausible nanosecond field. Only the
  // first 512 bytes are scanned; image pixels are never hashed or copied here.
  if (size < 512 || !std::getenv("CHIMAERA_PIPELINE")) return;
  const auto* p = static_cast<const unsigned char*>(data);
  for (size_t i = 12; i + 13 <= std::min(size, size_t(512)); ++i) {
    unsigned stream = 0;
    if (le32(p + i - 4) == 12 && !std::memcmp(p + i, "left_camera", 12)) stream = 1;
    if (le32(p + i - 4) == 13 && !std::memcmp(p + i, "right_camera", 13)) stream = 2;
    if (!stream || le32(p + i - 8) >= 1000000000) continue;
    const uint64_t capture = uint64_t(le32(p + i - 12)) * 1000000000 + le32(p + i - 8);
    if (at.sim == unset) at = stamp();
    if (at.sim == unset) return;
    std::lock_guard lock(mutex);
    if (!finished) {
      if (rows.size() >= 32768) throw std::runtime_error("pipeline trace overflow");
      rows.push_back({stage, stream, capture, size, at});
    }
    return;
  }
}
void flush() {
  std::lock_guard lock(mutex);
  if (!clock_fn || finished) return;
  finished = true;
  std::ostringstream out;
  out << "pid,stage,stream,capture_ns,bytes,sim_ns,wall_ns,thread_cpu_ns\n";
  for (const auto& r : rows)
    out << getpid() << ',' << r.stage << ',' << r.stream << ',' << r.capture << ','
        << r.bytes << ',' << r.at.sim << ',' << r.at.wall << ',' << r.at.cpu << '\n';
  const auto name = "pipeline-" + std::to_string(getpid()) + ".csv";
  if (write_fn) write_fn(name, out.str());
  else if (const char* directory = std::getenv("CHIMAERA_PIPELINE_DIR")) {
    std::ofstream file(std::string(directory) + "/" + name);
    file << out.str();
    if (!file) throw std::runtime_error("cannot export pipeline trace");
  }
}
}
