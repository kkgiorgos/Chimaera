// Run with verify_checkpoint_barrier.py and a static libm5 build.
#include <chimaera_ros_bridge/checkpoint_barrier.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include <cstdio>
#include <cstring>
#include <string>

int main(int argc, char ** argv)
{
  bool address = false, quiesce = false;
  for (int index = 1; index < argc; ++index) {
    address |= std::strcmp(argv[index], "--address") == 0;
    quiesce |= std::strcmp(argv[index], "--quiesce") == 0;
  }
  if (address) {
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
    m5_work_begin_addr(0, 0);
    // KVM can run beyond workbegin before the host completes the CPU switch.
    // Match production's bootstrap barrier before issuing the checkpoint.
    for (;;) {
      char data[96]{};
      const auto size = m5_read_file_addr(data, sizeof(data) - 1, 0);
      if (std::string(data, size) == "CHIMAERA_CHECKPOINT\n") break;
    }
    m5_checkpoint_addr(0, 0);
  } else {
    m5_checkpoint(0, 0);
  }
  unsigned polls = 0, reads = 0;
  chimaera_ros_bridge::wait_for_checkpoint_restore(
    [address, &reads] {
      char data[96]{};
      const auto size = address ? m5_read_file_addr(data, sizeof(data) - 1, 0)
                                : m5_read_file(data, sizeof(data) - 1, 0);
      if (address && ++reads == 1)
        m5_write_file_addr(data, size, 0, "checkpoint-gate-control.txt");
      return std::string(data, size);
    },
    [address, quiesce, &polls] {
      // Tell the full-system harness the guest has actually executed its
      // wait loop; saving immediately after the checkpoint can miss it.
      if (address && ++polls == 16) m5_work_end_addr(0, 0);
      if (quiesce) {
        if (address) m5_quiesce_ns_addr(1000);
        else m5_quiesce_ns(1000);
      }
    }, [] { return true; });
  puts("CHECKPOINT_BARRIER_RESTORE_OK");
  fflush(stdout);
  if (address) {
    const char passed[] = "CHECKPOINT_BARRIER_RESTORE_OK\n";
    m5_write_file_addr(const_cast<char *>(passed), sizeof(passed) - 1, 0,
                       "checkpoint-gate-restored.txt");
    m5_exit_addr(0);
  }
  else m5_exit(0);
}
