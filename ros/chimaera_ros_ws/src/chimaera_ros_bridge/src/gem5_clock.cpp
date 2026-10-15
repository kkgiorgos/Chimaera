#include <gem5/m5ops.h>

#include <cstdint>

// Loaded only by applications explicitly running on a detailed gem5 CPU.
// Native applications never call a simulator instruction.
extern "C" uint64_t chimaera_gem5_time_ns() { return m5_rpns(); }
extern "C" void chimaera_gem5_dram_node(uint64_t role)
{
  m5_chimaera_test(0x524f534452414dULL, role);
}
