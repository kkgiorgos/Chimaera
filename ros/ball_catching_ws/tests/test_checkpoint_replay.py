"""The replay stream must preserve interval boundaries and reject corruption."""
from pathlib import Path
import subprocess


def test_guest_checkpoint_waits_for_restore_without_transport_during_drain(tmp_path):
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / 'barrier.cpp'
    source.write_text(r'''
#include <chimaera_ros_bridge/checkpoint_barrier.hpp>
#include <stdexcept>
#include <string>
int main() {
  unsigned reads = 0, pauses = 0, handshakes = 0;
  bool restored = false;
  auto read = [&] {
    ++reads;
    return restored ? std::string("CHIMAERA_TIME_ORIGIN_NS 123\n")
                    : std::string("CHIMAERA_CHECKPOINT\n");
  };
  // Model a drain resuming the guest before serialization. The readfile
  // still requests capture: no new transport session may start yet.
  chimaera_ros_bridge::wait_for_checkpoint_restore(read, [&] {
    if (handshakes) throw std::runtime_error("handshake during drain");
    if (++pauses == 3) restored = true;
  }, [] { return true; });
  ++handshakes;
  if (reads != 4 || pauses != 3 || handshakes != 1)
    throw std::runtime_error("restore barrier not observed");
  // A saved PC before the wait must also resume immediately after restore.
  chimaera_ros_bridge::wait_for_checkpoint_restore(read,
    [] { throw std::runtime_error("restored guest must not wait"); }, [] { return true; });
  restored = false;
  bool cancelled = false;
  try {
    chimaera_ros_bridge::wait_for_checkpoint_restore(read, [] {}, [] { return false; });
  } catch (const std::runtime_error &) { cancelled = true; }
  if (!cancelled) throw std::runtime_error("cancel ignored");
}
''')
    binary = tmp_path / 'barrier'
    subprocess.run(['g++', '-std=c++20',
        '-I' + str(root / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge/include'),
        str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_native_replay_round_trip_and_failure_paths(tmp_path):
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / 'replay.cpp'
    source.write_text(r'''
#include <chimaera_ros_bridge/checkpoint_replay.hpp>
#include <filesystem>
#include <stdexcept>
#include <string>
using chimaera_ros_bridge::CheckpointReplay;
void check(bool ok) { if (!ok) throw std::runtime_error("replay assertion"); }
int main(int argc, char **argv) {
  const std::string path(argv[1]);
  const chimaera::Message frame{std::byte{1}, std::byte{2}, std::byte{3}, std::byte{4}};
  {
    CheckpointReplay record(path, true);
    record.append(frame);
    record.append(frame);
    record.boundary();
    record.boundary();
    record.finish();
  }
  {
    CheckpointReplay replay(path, false);
    auto first = replay.next();
    check(first && first->size() == 2 && first->at(0) == frame && first->at(1) == frame);
    auto second = replay.next();
    check(second && second->empty());
    check(!replay.next());
  }
  std::filesystem::resize_file(path, std::filesystem::file_size(path) - 1);
  bool rejected = false;
  try {
    CheckpointReplay replay(path, false);
    while (replay.next()) {}
  } catch (const std::runtime_error &) { rejected = true; }
  check(rejected);
  {
    CheckpointReplay record(path, true);
    record.append(frame);
    rejected = false;
    try { record.finish(); } catch (const std::logic_error &) { rejected = true; }
    check(rejected);
  }
}
''')
    binary = tmp_path / 'replay'
    subprocess.run(['g++', '-std=c++20', '-I' + str(root / 'gem5-transport/include'),
        '-I' + str(root / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge/include'),
        str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary), str(tmp_path / 'replay.bin')], check=True)
