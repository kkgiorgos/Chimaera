#include <cstdint>

namespace
{
uint64_t time_ns{};
}
extern "C" uint64_t chimaera_gem5_time_ns() { return time_ns; }
extern "C" void chimaera_test_set_time_ns(uint64_t value) { time_ns = value; }
