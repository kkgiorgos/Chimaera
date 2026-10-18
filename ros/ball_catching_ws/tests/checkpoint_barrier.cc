// Run with verify_checkpoint_barrier.py and a static libm5 build.
#include <chimaera_ros_bridge/checkpoint_barrier.hpp>
#include <gem5/m5ops.h>
#include <cstdio>
#include <string>

int main()
{
  m5_checkpoint(0, 0);
  chimaera_ros_bridge::wait_for_checkpoint_restore(
    [] {
      char data[96]{};
      const auto size = m5_read_file(data, sizeof(data) - 1, 0);
      return std::string(data, size);
    },
    [] { m5_quiesce_ns(1000); }, [] { return true; });
  puts("CHECKPOINT_BARRIER_RESTORE_OK");
  fflush(stdout);
  m5_exit(0);
}
