#include "ball_catching_sim/command_replay.hpp"
#include <cassert>
#include <filesystem>
#include <unistd.h>
int main() {
  const auto path = (std::filesystem::temp_directory_path() /
    ("chimaera-command-replay-" + std::to_string(getpid()))).string();
  ball_catching::CommandState expected;
  expected.torque = {1,2,3,4,5,6,7}; expected.ready = true; expected.stamp = .123456789;
  { ball_catching::CommandReplay capture(path, true);
    capture.apply(0, expected); expected.cameras = true; capture.apply(500000, expected); }
  ball_catching::CommandState state;
  { ball_catching::CommandReplay replay(path, false);
    assert(!replay.replay_complete());
    assert(replay.apply(0, state)); assert(state.ready && !state.cameras);
    assert(replay.apply(500000, state)); assert(state.cameras);
    assert(state.torque == expected.torque && state.stamp == expected.stamp);
    assert(replay.replay_complete()); assert(replay.finish_replay(state));
    assert(!replay.finish_replay(state)); assert(!replay.apply(1000000, state)); }
  bool rejected = false;
  try { ball_catching::CommandReplay replay(path, false); replay.apply(1, state); }
  catch (const std::runtime_error&) { rejected = true; }
  assert(rejected);
  { std::ofstream bad(path); bad << "CHIMAERA_COMMANDS 1\n0 1 0 -1 1 2\n"; }
  rejected = false;
  try { ball_catching::CommandReplay replay(path, false); }
  catch (const std::runtime_error&) { rejected = true; }
  assert(rejected); std::filesystem::remove(path); std::filesystem::remove(path + ".tail");
}
