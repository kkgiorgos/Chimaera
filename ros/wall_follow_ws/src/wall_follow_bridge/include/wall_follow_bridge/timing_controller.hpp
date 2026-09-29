#pragma once

#include <ignition/msgs/boolean.pb.h>
#include <ignition/msgs/world_control.pb.h>
#include <ignition/msgs/world_stats.pb.h>

#include <chimaera/gem5_controller.hpp>
#include <condition_variable>
#include <ignition/transport/Node.hh>
#include <limits>
#include <mutex>
#include <thread>

namespace wall_follow_bridge
{
// Serial, explicit coupling: guest consumes the previous Gazebo boundary;
// its returned commands are delivered before advancing the next physics interval.
class TimingController final : public chimaera::TimingController
{
public:
  TimingController(
    std::string socket, std::chrono::seconds timeout, chimaera::DataProducer & producer,
    chimaera::DataConsumer & consumer, std::string world, chimaera::Duration physics_step)
  : gem5_(std::move(socket), timeout),
    host_(gem5_, producer, consumer),
    timeout_(timeout),
    physics_step_(physics_step),
    world_(std::move(world))
  {
    if (physics_step_.count() <= 0 || world_.empty()) {
      throw std::invalid_argument("physics_step_ns must be positive and gazebo_world nonempty");
    }
    if (!gazebo_.Subscribe("/world/" + world_ + "/stats", &TimingController::stats, this)) {
      throw std::runtime_error("Cannot subscribe to Gazebo world statistics");
    }
  }
  ~TimingController() override { gazebo_.Unsubscribe("/world/" + world_ + "/stats"); }

  void wait_until_ready(std::chrono::seconds timeout, const std::function<bool()> & cancelled)
  {
    gem5_.wait_until_ready(timeout, cancelled);
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    std::unique_lock lock(mutex_);
    while (!seen_) {
      lock.unlock();
      if (cancelled && cancelled()) {
        throw std::runtime_error("Startup cancelled");
      }
      lock.lock();
      if (std::chrono::steady_clock::now() >= deadline) {
        throw std::runtime_error("Timed out waiting for Gazebo statistics");
      }
      changed_.wait_for(lock, std::chrono::milliseconds(50));
    }
    if (!paused_) {
      throw std::runtime_error("Gazebo must start paused (omit -r)");
    }
  }

  void start(chimaera::Duration interval) override
  {
    if (
      pending_ || interval.count() <= 0 || interval > std::chrono::hours(1) ||
      interval.count() % physics_step_.count() != 0 ||
      interval / physics_step_ > std::numeric_limits<uint32_t>::max()) {
      throw std::invalid_argument(
        "Interval must be an integral number of physics steps, <= 1 hour; no overlapping steps");
    }
    interval_ = interval;
    pending_ = true;
    const auto result = host_.step(interval_, poll_);
    if (!result.ok()) {
      throw std::runtime_error(result.message);
    }
    // DDS and the host command gateway run asynchronously to the timing barrier.
    // Allow their executors to consume commands while both simulators are paused.
    settle();
    {
      std::lock_guard lock(mutex_);
      if (!seen_ || !paused_) {
        throw std::runtime_error("Gazebo is not paused");
      }
      target_ = iterations_ + interval_ / physics_step_;
      target_time_ = time_ns_ + interval_.count();
    }
    ignition::msgs::WorldControl request;
    request.set_pause(true);
    request.set_multi_step(static_cast<uint32_t>(interval_ / physics_step_));
    ignition::msgs::Boolean response;
    bool accepted = false;
    if (
      !gazebo_.Request(
        "/world/" + world_ + "/control", request,
        static_cast<unsigned int>(
          std::chrono::duration_cast<std::chrono::milliseconds>(timeout_).count()),
        response, accepted) ||
      !accepted || !response.data()) {
      throw std::runtime_error("Gazebo rejected or timed out accepting a step");
    }
  }

  void wait() override
  {
    if (!pending_) {
      throw std::logic_error("wait without start");
    }
    std::unique_lock lock(mutex_);
    if (!changed_.wait_for(lock, timeout_, [&] { return iterations_ >= target_ && paused_; })) {
      throw std::runtime_error("Gazebo did not finish the requested physics steps");
    }
    if (iterations_ != target_ || time_ns_ != target_time_) {
      throw std::runtime_error(
        "Gazebo step mismatch: check physics_step_ns and exclusive world control");
    }
    lock.unlock();
    settle();
    pending_ = false;
  }

  chimaera::ControllerResult step(chimaera::Duration interval, chimaera::Duration poll)
  {
    try {
      if (poll.count() <= 0 || poll >= interval) {
        throw std::invalid_argument("Invalid poll interval");
      }
      poll_ = poll;
      start(interval);
      wait();
      return {};
    } catch (const std::exception & error) {
      return {chimaera::ControllerState::failed, error.what()};
    }
  }
  chimaera::ControllerResult stop() { return host_.stop(); }
  uint64_t elapsed_ticks() const { return gem5_.elapsed_ticks(); }
  std::function<void()> pump;

private:
  void settle()
  {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(20);
    do {
      if (pump) {
        pump();
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    } while (std::chrono::steady_clock::now() < deadline);
  }
  void stats(const ignition::msgs::WorldStatistics & message)
  {
    std::lock_guard lock(mutex_);
    seen_ = true;
    paused_ = message.paused();
    iterations_ = message.iterations();
    time_ns_ = message.sim_time().sec() * 1000000000LL + message.sim_time().nsec();
    changed_.notify_all();
  }
  chimaera::Gem5TimingController gem5_;
  chimaera::Gem5HostController host_;
  std::chrono::seconds timeout_;
  chimaera::Duration physics_step_, interval_{}, poll_{std::chrono::milliseconds(10)};
  std::string world_;
  std::mutex mutex_;
  std::condition_variable changed_;
  bool seen_{false}, paused_{false}, pending_{false};
  uint64_t iterations_{}, target_{};
  int64_t time_ns_{}, target_time_{};
  ignition::transport::Node gazebo_;
};
}  // namespace wall_follow_bridge
