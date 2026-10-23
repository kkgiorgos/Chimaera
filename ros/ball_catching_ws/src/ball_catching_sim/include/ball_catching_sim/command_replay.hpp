#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace ball_catching
{
struct CommandState
{
  std::array<double, 7> torque{};
  double stamp{-1};
  bool ready{}, cameras{};
};
// Reproduce exactly the commands consumed by physics, without reconstructing
// the old external DDS participants. No additional ROS subscriptions or guest
// work. Only the aggressor-free, pre-throw prefix is recorded/replayed.
class CommandReplay
{
  struct Record { int64_t time; CommandState state; };
  std::ofstream output_;
  std::vector<Record> records_;
  std::size_t next_{};
  int64_t previous_{-1};
  std::string tail_path_;
  CommandState tail_;
  bool tail_applied_{};
  static constexpr std::size_t max_records = 1000000;
public:
  CommandReplay(const std::string & path, bool capture)
  : tail_path_(path + ".tail")
  {
    if (capture) {
      output_.open(path);
      output_ << "CHIMAERA_COMMANDS 1\n" << std::setprecision(17);
      if (!output_) throw std::runtime_error("Cannot create checkpoint command prefix");
    } else {
      std::ifstream input(path);
      std::string line;
      if (!std::getline(input, line) || line != "CHIMAERA_COMMANDS 1")
        throw std::runtime_error("Invalid checkpoint command prefix");
      int64_t previous = -1;
      while (std::getline(input, line)) {
        std::istringstream fields(line);
        Record record{};
        unsigned ready, cameras;
        if (!(fields >> record.time >> ready >> cameras >> record.state.stamp) || ready > 1 ||
            cameras > 1 || record.time <= previous || !std::isfinite(record.state.stamp))
          throw std::runtime_error("Invalid checkpoint command record");
        record.state.ready = ready; record.state.cameras = cameras;
        for (auto & torque : record.state.torque)
          if (!(fields >> torque) || !std::isfinite(torque))
            throw std::runtime_error("Invalid checkpoint torque");
        std::string extra;
        if ((fields >> extra) || records_.size() == max_records)
          throw std::runtime_error("Checkpoint command prefix exceeds bounds");
        records_.push_back(record); previous = record.time;
      }
      if (!input.eof() || records_.empty()) throw std::runtime_error("Incomplete checkpoint command prefix");
      std::ifstream tail(tail_path_);
      unsigned ready, cameras;
      if (!(tail >> ready >> cameras >> tail_.stamp) || ready > 1 || cameras > 1 || !std::isfinite(tail_.stamp))
        throw std::runtime_error("Invalid checkpoint command tail");
      tail_.ready = ready; tail_.cameras = cameras;
      for (auto & torque : tail_.torque)
        if (!(tail >> torque) || !std::isfinite(torque)) throw std::runtime_error("Invalid checkpoint tail torque");
      if (tail >> line) throw std::runtime_error("Trailing checkpoint command tail");
    }
  }
  bool apply(int64_t time, CommandState & state)
  {
    if (output_.is_open()) {
      if (time <= previous_ || next_++ == max_records)
        throw std::runtime_error("Invalid checkpoint physics boundary");
      previous_ = time;
      output_ << time << ' ' << state.ready << ' ' << state.cameras << ' ' << state.stamp;
      for (auto torque : state.torque) output_ << ' ' << torque;
      output_ << '\n'; output_.flush();
      if (!output_) throw std::runtime_error("Cannot save checkpoint commands");
      observe(state);
      return true;
    }
    if (next_ == records_.size()) return false;
    const auto & record = records_[next_++];
    if (record.time != time) throw std::runtime_error("Checkpoint command/physics time mismatch");
    state = record.state;
    return true;
  }
  bool replay_complete() const { return !output_.is_open() && next_ == records_.size(); }
  void observe(const CommandState & state)
  {
    if (!output_.is_open()) return;
    std::ofstream tail(tail_path_);
    tail << std::setprecision(17) << state.ready << ' ' << state.cameras << ' ' << state.stamp;
    for (auto torque : state.torque) tail << ' ' << torque;
    tail << '\n'; tail.flush();
    if (!tail) throw std::runtime_error("Cannot save pending checkpoint command state");
  }
  bool finish_replay(CommandState & state)
  {
    if (!replay_complete() || tail_applied_) return false;
    state = tail_; tail_applied_ = true;
    return true;
  }
};
}  // namespace ball_catching
