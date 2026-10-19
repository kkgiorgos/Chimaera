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
void ensure_mapping() { static Mapping mapping; }
}
extern "C" uint64_t chimaera_gem5_time_ns() {
  ensure_mapping();
  return m5_rpns_addr();
}
extern "C" void chimaera_gem5_case_end() {
  ensure_mapping();
  m5_work_end_addr(0, 0);
}
extern "C" uint64_t chimaera_gem5_read_file(void * data, uint64_t size, uint64_t offset) {
  ensure_mapping();
  return m5_read_file_addr(data, size, offset);
}
#else
extern "C" uint64_t chimaera_gem5_time_ns() { return m5_rpns(); }
extern "C" void chimaera_gem5_case_end() { m5_work_end(0, 0); }
extern "C" uint64_t chimaera_gem5_read_file(void * data, uint64_t size, uint64_t offset) {
  return m5_read_file(data, size, offset);
}
#endif
