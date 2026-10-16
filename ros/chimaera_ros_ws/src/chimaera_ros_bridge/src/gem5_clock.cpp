#include <gem5/m5ops.h>

#include <cstdint>

// Native applications never load this library. Address operations also work
// during KVM readiness; instruction operations require a detailed CPU.
#ifdef CHIMAERA_CLOCK_ADDRESS
#include <m5_mmap.h>
namespace {
struct Mapping {
  Mapping() { m5op_addr = 0xFFFF0000; map_m5_mem(); }
  ~Mapping() { unmap_m5_mem(); }
};
}
extern "C" uint64_t chimaera_gem5_time_ns() {
  static Mapping mapping;
  return m5_rpns_addr();
}
#else
extern "C" uint64_t chimaera_gem5_time_ns() { return m5_rpns(); }
#endif
