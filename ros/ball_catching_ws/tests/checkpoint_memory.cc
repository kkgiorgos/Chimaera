// Native gem5 regression: dirty L1/L2 data must survive a readiness checkpoint.
#include <gem5/m5ops.h>
#include <stdint.h>
#include <stdio.h>

static volatile uint64_t values[131072];

int main(void)
{
  for (uint64_t i = 0; i < 131072; ++i)
    values[i] = (i * UINT64_C(0x9e3779b97f4a7c15)) ^ UINT64_C(0x123456789abcdef0);
  m5_checkpoint(0, 0);
  for (uint64_t i = 0; i < 131072; ++i) {
    const uint64_t expected = (i * UINT64_C(0x9e3779b97f4a7c15)) ^ UINT64_C(0x123456789abcdef0);
    if (values[i] != expected) {
      puts("CHECKPOINT_MEMORY_CORRUPT");
      fflush(stdout);
      m5_fail(0, 1);
      return 1;
    }
  }
  puts("CHECKPOINT_MEMORY_OK");
  fflush(stdout);
  m5_exit(0);
  return 0;
}
