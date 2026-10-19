#include <chrono>
#include <ignition/gazebo/System.hh>
#include <ignition/plugin/Register.hh>
#include <memory>

#include "shared_clock.hpp"

namespace chimaera_latency
{
class PhysicsClock : public ignition::gazebo::System,
                     public ignition::gazebo::ISystemConfigure,
                     public ignition::gazebo::ISystemPreUpdate
{
public:
  void Configure(
    const ignition::gazebo::Entity &, const std::shared_ptr<const sdf::Element> & sdf,
    ignition::gazebo::EntityComponentManager &, ignition::gazebo::EventManager &) override
  {
    clock_ = std::make_unique<SharedClock>(sdf->Get<std::string>("clock_file"), true);
  }
  void PreUpdate(
    const ignition::gazebo::UpdateInfo & info, ignition::gazebo::EntityComponentManager &) override
  {
    // Keep the clock invalid during discovery in a newly paused world.
    if (!info.paused) {
      clock_->set(std::chrono::duration_cast<std::chrono::nanoseconds>(info.simTime).count());
    }
  }

private:
  std::unique_ptr<SharedClock> clock_;
};
}  // namespace chimaera_latency

IGNITION_ADD_PLUGIN(
  chimaera_latency::PhysicsClock, ignition::gazebo::System,
  chimaera_latency::PhysicsClock::ISystemConfigure,
  chimaera_latency::PhysicsClock::ISystemPreUpdate)
