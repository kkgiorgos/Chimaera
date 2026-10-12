# Wall-follow shared-memory contention benchmark

This study holds the CPU, caches, DDR4 controller and robot settings fixed and
varies traffic from an independent synthetic accelerator. It uses the pinned
Ramulator 2.1 backend and full gem5/ROS/Gazebo co-simulation. No architecture
sweep is required.

## Run

```bash
python3 containers/build.py --tag chimaera:ramulator2 --jobs 3
python3 containers/wall-follow/suite.py --image chimaera:ramulator2 \
  --config ros/wall_follow_ws/experiments/memory_contention.json \
  --output results/memory-contention --workers 1
```

The suite builds the ROS overlay and prepares a matching guest. Guest disks are
immutable, and must never be modified while a simulator uses them. Container
workers have isolated transport paths; native execution permits one session.
Use `--resume` to retry failed runs while retaining successful attempts.

## Workloads

The default light DSE has eight designs and two independent repetitions each:

| Traffic | Interval (DRAM cycles) | Reads | Duty |
| --- | ---: | ---: | ---: |
| Baseline | disabled | — | — |
| Coalesced stream | 16, 4 | 80% | 100% |
| Strided gather | 16, 4 | 80% | 100% |
| Random gather | 16, 4 | 80% | 100% |
| Write-heavy bursts | 1 | 20% | 25% |

The controller is fixed DDR4-2400, one channel/rank, FRFCFS, open rows and
all-bank refresh. Two timing CPUs run at 3 GHz with fixed 16 KiB L1 caches and
256 KiB L2. Control and lidar are 20 Hz with 720 beams and zero configured
sensor noise. Each task runs for four simulated seconds. This is a screening
experiment, not a long-horizon navigation validation or a hardware-calibrated
GPU model.
The host-time limit is three hours per attempt so saturated traffic can complete
the same four-second simulated task. This limit does not change simulated timing.

## Customize

Use `fixed` and bundled `sweep` fields in the experiment JSON. The aggressor
parameters are:

| Parameter | Meaning | Default |
| --- | --- | ---: |
| `aggressor_pattern` | `none`, `stream`, `stride`, `random` | `none` |
| `aggressor_interval` | One offered transaction every N DRAM cycles | 4 |
| `aggressor_stride` | Byte stride for strided traffic; transaction aligned | 4096 |
| `aggressor_read_percent` | Reads in each 100 accepted requests; others writes | 80 |
| `aggressor_max_pending` | Accelerator outstanding request cap | 128 |
| `aggressor_window` | Address window in bytes above exposed guest memory | 268435456 |
| `aggressor_seed` | Nonzero deterministic xorshift random seed | 1 |
| `aggressor_duty_percent` | Active fraction of a periodic burst | 100 |
| `aggressor_period` | Burst period in DRAM cycles | 12000 |

The stream advances by one DRAM transaction, stride advances by the configured
stride, and random chooses an aligned transaction in the address window.
Read/write mixing uses a repeating block of 100 accepted transactions, so it
also produces short read/write phases. Queue-full offers are dropped and counted;
this is an open-loop offered-load source, not a lossless accelerator trace.
Stride/stream progression advances only on acceptance. Random draws advance on
submission attempts. Sources stop issuing during drain and remain inactive in
atomic/KVM boot; they start after the timing CPU switch at the ROI.

Device and CPU transactions enter Ramulator's External frontend with its supported
source ID 0. The wrapper separates their ownership, callbacks and metrics.
Upstream per-source counters combine both streams; schedulers that need distinct
source IDs require an expanded frontend and are outside this study. They share
the actual controller queues, banks,
scheduler and DRAM timing. The device has a separate outstanding cap from the
CPU wrapper. Synthetic writes change DRAM timing state only: they do not write
guest backing-store data. The disjoint window must fit in the DRAM capacity.
There are no GPU shader cores, page migrations, coherence interactions, shared
cache effects, or guest CPU aggressor threads. The GPU analogy is access pattern
and memory-controller sharing, including the case of unified physical memory.

For a custom DRAM export, set `memory_backend` to `ramulator2` and
`ramulator_config` to a fully expanded Ramulator 2.1 JSON with the External
frontend. Container configuration snapshots custom paths relative to the
experiment JSON and mounts them read-only. Every attempt also retains
`ramulator.json` and its SHA-256. The supplied container preset is
`/opt/chimaera/ramulator2/ddr4-2400.json`.

For native runs, use the workspace's `scripts/run_experiments.py --gem5` and
point `ramulator_config` at a local export, with the usual guest and kernel
arguments. Paths can be relative to the experiment JSON. See
[Ramulator integration](../gem5/ext/ramulator2/README.md) for native builds.

## Read measurements

The offline dashboard is `<output>/comparison/dashboard.html`. Select workloads
and compare task, memory and simulation charts; export group summaries as JSON.
It aggregates run-level metrics across repetitions, displays sample standard
deviations, and includes traces and differing settings. The memory section also
shows percentage changes from a matching baseline; mismatched robot, DRAM,
hardware or source settings do not produce a baseline percentage.

- **Task:** wall-distance RMSE/MAE, distance travelled, command receipt gap p95
  and maximum, first command receipt in world simulated seconds, and fraction of
  positive command gaps above 1.5 control periods. World time starts at zero in
  these runs; first command time includes guest application/bridge startup and
  is reported even when warmup excludes initial tracking samples.
  Command gaps use simulated time and ignore duplicate receipt timestamps.
  They measure the bridge/host's delivered control updates, not controller
  execution time or true internal deadline misses. Co-simulation quantization
  and startup can dominate small differences. Host scan age is the age of the
  host's latest scan at receipt, not the controller's consumed scan.
- **Memory:** accepted aggressor bytes per simulated ROI second (decimal GB/s),
  rejected offers, completed guest DRAM reads, mean guest read latency and guest
  submission retries. Guest latency spans acceptance-to-completion in Ramulator,
  includes all guest clients, and excludes time before a rejected submission is
  retried. A separate guest retry count captures those rejections. Write callbacks
  may represent forwarding/coalescing, so accepted GB/s is not a DRAM bus counter.
- **Simulation:** simulated seconds per wall second and phase wall times.
  Simulator slowdown is not robot task degradation. The robot may remain robust
  even when memory latency rises significantly.

Raw `samples.csv`, `poses.csv`, `timing.csv`, gem5 stats, Ramulator YAML snapshots,
serial logs and attempt metadata remain beside each attempt. Memory statistics
cover the complete post-workbegin ROI, while robot metrics begin at collection
and honor report warmup; their windows are related but not identical.

## Checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=ros/wall_follow_ws/benchmarking \
  python3 -m pytest -q ros/wall_follow_ws/tests containers/tests containers/wall-follow/tests
python3 gem5/ext/ramulator2/tests/run.py --gem5 /path/to/gem5.opt \
  --output /tmp/ramulator-tests
node ros/wall_follow_ws/tests/dashboard_smoke.cjs results/memory-contention/comparison/dashboard.html
```

The memory regressions compare reads against an independent reference under
retry pressure, check all three aggressor patterns and ensure saturated device
traffic can drain.

## Measured validation

All **16/16 runs completed**: eight workloads, two repetitions each, four simulated seconds per task. CPU/cache/DDR4 and robot settings were fixed. Values below are repetition means ± sample SD; two repetitions are screening evidence.

| Workload | Accepted GB/s | Guest read ns | Command gap p95 ms | Tracking RMSE m | Progress m |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline · no aggressor | 0.000 ± 0.000 | 33.377 ± 0.445 | 100.000 ± 0.000 | 0.1373 ± 0.0004 | 1.2674 ± 0.0149 |
| Random · every 16 DRAM cycles · 80% reads · 100% duty | 4.802 ± 0.000 | 52.990 ± 0.248 | 75.000 ± 35.355 | 0.1359 ± 0.0003 | 1.2769 ± 0.0005 |
| Random · every 4 DRAM cycles · 80% reads · 100% duty | 11.014 ± 0.000 | 262.851 ± 0.056 | 100.000 ± 0.000 | 0.1369 ± 0.0015 | 1.2670 ± 0.0143 |
| Stream · every 1 DRAM cycles · 20% reads · 25% duty | 3.827 ± 0.001 | 56.789 ± 0.837 | 75.000 ± 35.355 | 0.1356 ± 0.0002 | 1.2852 ± 0.0122 |
| Stream · every 16 DRAM cycles · 80% reads · 100% duty | 4.802 ± 0.000 | 45.147 ± 0.182 | 50.000 ± 0.000 | 0.1359 ± 0.0012 | 1.2852 ± 0.0122 |
| Stream · every 4 DRAM cycles · 80% reads · 100% duty | 13.926 ± 0.002 | 87.198 ± 1.099 | 75.000 ± 35.355 | 0.1364 ± 0.0009 | 1.2772 ± 0.0010 |
| Stride · every 16 DRAM cycles · 80% reads · 100% duty | 4.802 ± 0.000 | 46.565 ± 0.018 | 75.000 ± 35.355 | 0.1360 ± 0.0004 | 1.2769 ± 0.0005 |
| Stride · every 4 DRAM cycles · 80% reads · 100% duty | 15.845 ± 0.001 | 193.068 ± 0.264 | 75.000 ± 35.355 | 0.1365 ± 0.0003 | 1.2767 ± 0.0002 |

**Artifacts:** [interactive offline dashboard](examples/wall-follow-memory-contention-dashboard.html), [group summaries](examples/wall-follow-memory-contention-summary.json). Complete raw attempts and exact configurations are retained under `results/memory-contention/` and `results/memory-contention-long-budget/`; the combined selection is `results/memory-contention/comparison/coverage.json`.

**Execution:** pinned image `sha256:564ae1412459190627f6249bdad81eae48cf06426259c3ebe95bb0b825045b6a`; binary/source/guest hashes and the two immutable execution plans are recorded in `results/memory-contention/provenance.json`. Two original attempts were interrupted before completion when the predicted saturated runtime exceeded the original one-hour host budget. Their partial data is retained and excluded. The remaining 14 runs used a three-hour limit with three isolated workers. These host execution changes did not change simulated workloads or timing.

**Checks:** 206 Python workflow/container tests passed; all ten Ramulator regression cases passed, including stream/stride/random retry pressure and saturated-traffic draining. Offline dashboard chart/selection/metric interactions passed against the measured results. Command gaps reflect delivered bridge updates and co-simulation quantization; read latency is the accepted guest transaction latency, not full application execution time.

The measured mean guest read latency rose from 33.38 ns to 262.85 ns (7.88×),
while tracking RMSE means stayed between 0.1356 and 0.1373 m. Delivered command
gaps were variable across repetitions, and do not establish internal controller
deadline misses. This short screening study shows substantial DRAM interference
with small observed changes in tracking error; longer tasks and finer
co-simulation intervals would be separate follow-up studies.
