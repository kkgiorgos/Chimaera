# Ramulator 2.1 integration

This optional backend supplies Ramulator's DRAM controllers to the repository's
modified gem5 25.1.0.1. It supports timing and O3 CPUs, classic and Ruby memory
ports, and atomic/KVM boot followed by a switch to timing execution.

The single-port and vector-port wrappers are adapted from
[CMU-SAFARI/ramulator2](https://github.com/CMU-SAFARI/ramulator2) at the commit
recorded in `REVISION` (`72427a1bba3771564c4fb0e494ba02242fd1eaa7`). Their MIT
license is in `../../src/mem/ramulator2/LICENSE`. The downloaded dependency and
its build products are ignored by Git.

## Build

From the repository root, with Git, CMake 3.22+, Python 3.10+, SCons and a
C++20-capable compiler installed:

```bash
bash gem5/ext/ramulator2/setup.sh 2
cd gem5
scons build/X86/gem5.opt -j2 RAMULATOR2_ROOT=ext/ramulator2/ramulator2
```

Setup downloads the pinned source and its upstream fmt/yaml-cpp dependencies.
It builds only `libramulator.so`; nanobind and Python bindings are unnecessary.
Use a compiler with the same C++ runtime ABI as gem5. Only the bridge source
that includes Ramulator headers compiles as C++20; gem5 stays at C++17.

Pass `RAMULATOR2_ROOT` on each SCons invocation that should enable this backend.
It accepts an absolute path or a path relative to `gem5/`. An invalid explicit
path fails the build. Omit it for a build without Ramulator. The enabled binary
uses an RPATH to find the library; keep that checkout in place, or configure
`LD_LIBRARY_PATH` after relocating it.

## Run without a guest image

From the repository root:

```bash
gem5/build/X86/gem5.opt --outdir=/tmp/ramulator-se \
  gem5/configs/example/gem5_library/ramulator2.py \
  --config gem5/ext/ramulator2/configs/ddr4-2400.json \
  --binary gem5/tests/test-progs/hello/bin/x86/linux/hello
```

Add `--cpu-type o3` to use O3. For custom stdlib boards:

```python
from gem5.components.memory.ramulator2 import Ramulator2Memory
memory = Ramulator2Memory("/absolute/path/exported.json", size="3GiB")
```

The supplied DDR4-2400 configuration was exported from the pinned Ramulator
Python DSL: `DDR4_8Gb_x8`, one rank, 64-bit channel, FRFCFS, all-bank refresh,
and open rows. It models 8 GiB; gem5 may expose a smaller contiguous range.
Use fully expanded Ramulator **2.1** JSON with the `External` frontend. Older
2.0 YAML presets have a different schema. Export custom configurations with
the upstream Python DSL (`python -m ramulator export --help`); the resulting
timing constraints must accompany any timing changes. Upstream
`ramulator.gem5.Memory` and `VectorPortMemory` also work with these SimObjects.

## Wall-follow integration

Add these arguments to the wall-follow session's `simulator.args` when using
`gem5_wall_follow.py`, alongside its existing CPU and image settings:

```json
["--memory-backend", "ramulator2",
 "--ramulator-config", "/absolute/path/ddr4-2400.json"]
```

Select `--cpu-type timing` or `o3`. KVM boot uses gem5's backing store and does
not advance the DRAM model; after the CPU switch, ticking resumes at the
configured DRAM period. The existing ROI statistics reset also resets Ramulator
counters. The default memory backend remains gem5's DDR3 controller.

## Validation and outputs

```bash
python3 gem5/ext/ramulator2/tests/run.py --output /tmp/ramulator21-tests
```

Regressions check read/write data against MemTest's reference memory, force
request retries with a two-slot pending limit, exercise two-channel vector
routing, run timing/O3 SE workloads, and switch an atomic CPU to timing before
draining. No guest image, ROS, or network downloads are needed for these tests.

With KVM access and the prepared native guest artifacts, add `--full-system`
to also boot the real two-core X86/Ruby system, switch at a guest workbegin
marker, and complete a timing ROI. Override `--kernel` and `--image` as needed.
The image must execute gem5 readfile at boot and provide `m5` on its PATH.

Each controller writes `<object>.ramulator_stats.yaml` at exit and
`<object>.ramulator_stats.<tick>.yaml` at gem5 statistics dumps. Object names
prevent collisions between controllers. Use `--debug-flags=Ramulator2` before
the config script for request tracing. `max_outstanding` bounds transactions
and queued responses, including responses held by downstream backpressure.
It must be at least two so a write and its acknowledgement both fit.

Packets must fit within one DRAM transaction; the wrapper does not split larger
cache lines. DRAM periods round to the nearest gem5 tick. gem5 checkpoints
preserve backing-store data, but Ramulator's internal bank/queue state is not
serialized: restore starts a fresh DRAM model. Full ROS/Gazebo experiments
require their usual workspace builds and guest artifacts.
