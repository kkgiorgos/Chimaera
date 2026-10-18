#pragma once

#include <stdexcept>

namespace chimaera_ros_bridge
{
// Checkpoint draining may resume the guest past its m5 checkpoint instruction.
// Stay off the transport until the restoring simulator replaces the readfile.
template<class ReadControl, class Pause, class Running>
void wait_for_checkpoint_restore(ReadControl read_control, Pause pause, Running running)
{
  while (read_control() == "CHIMAERA_CHECKPOINT\n") {
    if (!running()) throw std::runtime_error("Checkpoint restore wait cancelled");
    pause();
  }
}
}  // namespace chimaera_ros_bridge
