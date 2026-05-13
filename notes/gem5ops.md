# Custom gem5 ops

## Reasoning
We need a way to transfer data between the host and the simulated guest
system.

## Creation Process

### Step 1: Add a new pseudo-op number
Edit:
```
include/gem5/asm/generic/m5ops.h
```

There are reserved slots. Use them.

For example:

```c
#define M5OP_BRIDGE_NOTIFY M5OP_RESERVED1
```

Then add it to `M5OP_FOREACH`:

```c
#define M5OP_FOREACH                                      \
    M5OP(m5_arm, M5OP_ARM)                                \
    /* ... existing ops ... */                            \
    M5OP(m5_bridge_notify, M5OP_BRIDGE_NOTIFY)            \
```

### Step 2: Add the guest-facing C prototype
Edit:
```
include/gem5/m5ops.h
```

Add:
```c
void m5_bridge_notify(uint64_t channel, uint64_t value);
```

### Step 3: Add the simulator-side function declaration
Edit:
```
src/sim/pseudo_inst.hh
```

Add a declaration inside namespace gem5::pseudo_inst:

```c++
void bridgeNotify(ThreadContext *tc, uint64_t channel, uint64_t value);
```

### Step 4: Add the switch case

Still in:
```
src/sim/pseudo_inst.hh
```

Inside `pseudoInstWork(...)`, add:

```c++
case M5OP_BRIDGE_NOTIFY:
    invokeSimcall<ABI>(tc, bridgeNotify);
    return true;
```

### Step 5: Implement the handler
Edit:
```
src/sim/pseudo_inst.cc
```

Add:
```c++
void
bridgeNotify(ThreadContext *tc, uint64_t channel, uint64_t value)
{
    DPRINTF(PseudoInst,
            "pseudo_inst::bridgeNotify(channel=%llu, value=%llu)\n",
            channel, value);

    // Keep this host-side only.
    // Do not call exitSimLoop().
    // Do not schedule gem5 events.
    // Do not mutate architectural state.
    // Do not touch simulated memory unless this op is explicitly a data op.

    inform("bridgeNotify: tick=%llu cpu=%d channel=%llu value=%llu\n",
           curTick(),
           tc->getCpuPtr()->cpuId(),
           channel,
           value);
}
```

### Step 6: Rebuild gem5 and libm5

Rebuild gem5:
```
scons build/X86/gem5.opt -j"$(nproc)"
```

Then rebuild the m5 utility/library for the guest ABI:
```
cd util/m5
scons build/x86/out/m5
```

### Step 7: Call it from guest code

Example guest program:

```c++
#include <stdint.h>
#include <stdio.h>
#include <gem5/m5ops.h>

int main(void)
{
    printf("before custom m5op\n");

    m5_bridge_notify(3, 12345);

    printf("after custom m5op\n");
    return 0;
}
```

Compile:
```bash
GEM5=/path/to/gem5

gcc test_bridge.c \
    -I"$GEM5/include" \
    -L"$GEM5/util/m5/build/x86/out" \
    -lm5 \
    -o test_bridge
```

If running under KVM, use the _addr version:
```c++
#include <stdint.h>
#include <stdio.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

int main(void)
{
    m5op_addr = 0xFFFF0000;   // x86 magic address
    map_m5_mem();

    m5_bridge_notify_addr(3, 12345);

    unmap_m5_mem();
    return 0;
}
```

## Data Transfer

We need primitives to transfer raw bytes from the simulator's vmem to
a host buffer.

Inspecting the existing readfile and writefile leads us to readBlob and writeBlob.

After a very basic implementation we have verified transfer happens correctly
and the tick count inside gem5 isn't affected. Basically there's no
transfer time from the perspective of the sim.

Note: This has been tested only on KVM for now. 
    Also the transferred data is not used (it doesn't go anywhere useful).
