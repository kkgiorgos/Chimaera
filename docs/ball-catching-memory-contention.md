# Ball-catching memory interference suite

This local suite holds the computer, physics, cup, camera geometry and guest
software fixed while sweeping synthetic GPU-like DRAM traffic. Each configuration
produces one binary catch/miss result. It is a coarse demonstration of the task's
operating boundary, with no statistical reliability claim. Container execution
is a later step; configuration, per-attempt records and the report remain
independent of the local execution backend.

## Reference system and boundary

The guest runs perception, flight estimation, interception/braking planning and
effort control. The host runs Gazebo physics, stereo rendering, launching,
actuation and passive scoring. The bridge carries RGB images, simulation time,
joint feedback, efforts and readiness. Throw settings and ground truth never
cross into robot computation.

The suite enables the existing keyed coalescing mechanism only on `/clock`.
Both the ROS adapter and controller retain its newest pending update. Paused
Gazebo publishes repeated timestamps in wall time; without this policy they
can fill the adapter queue before a physics step completes. Image, joint and
robot-output routes retain their configured FIFO behaviour.

The reference is four x86 Timing CPUs at 3 GHz, private 32 KiB instruction/data
L1 caches and an 8 MiB shared Ruby MESI last-level cache. Ramulator models two
64-bit DDR4-3200AA channels, one 8 GiB rank each, FRFCFS, open rows and all-bank
refresh. X86Board exposes 3 GiB to the guest; the device uses a disjoint 256 MiB
window above it. This is a PC-sized parameter set, not a faithful model of a
specific commercial processor. Timing CPUs do not model its out-of-order core.

The preset uses 0.5 ms physics/exchange intervals, 100 us guest polling and fixed
1 kHz joint feedback. The experiment owns these settings, so every point uses
the same coupling quantization. The existing bridge waits for host callbacks at
boundaries; finer intervals increase that wall-time cost. A finer-step convergence
comparison is needed before interpreting small latency-sensitive task differences.

## Build and run

Use Ubuntu 22.04, ROS Humble, Gazebo Fortress and the existing native workspace
dependencies. Build the Ramulator-enabled simulator as described in
[the integration README](../gem5/ext/ramulator2/README.md), then:

```bash
source /opt/ros/humble/setup.bash
colcon --log-base ros/ball_catching_ws/log build \
  --base-paths ros/franka_ws/src/franka_description ros/ball_catching_ws/src \
  --build-base ros/ball_catching_ws/build \
  --install-base ros/ball_catching_ws/install \
  --cmake-args -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
colcon --log-base ros/chimaera_ros_ws/log build \
  --base-paths ros/chimaera_ros_ws/src \
  --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install \
  --cmake-args -DBUILD_TESTING=ON -DBUILD_GAZEBO_BRIDGE=ON -DCMAKE_BUILD_TYPE=Release
source ros/chimaera_ros_ws/install/local_setup.bash
source ros/ball_catching_ws/install/local_setup.bash

# Inspect all cases without booting, launching, or writing records.
python3 ros/ball_catching_ws/scripts/run_memory_suite.py --dry-run

# Cheap native estimates: only disabled-aggressor cases are eligible.
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --native-preflight --output ros/ball_catching_ws/results/memory-native

# Calibrate memory alone, without booting a guest or running Gazebo.
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --calibrate-only --output ros/ball_catching_ws/results/memory-study

# Run just a standard-rate 3 m/s baseline first.
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --output ros/ball_catching_ws/results/memory-study --resume --case case-003

# Add its strongest aggressor point after inspecting the baseline.
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --output ros/ball_catching_ws/results/memory-study --resume --case case-005
```

Omit `--case` to run the full 12-point preset. Native Chimaera sessions run
serially because their data sockets are fixed. `--image`, `--kernel`,
`--gem5-root` and `--config` override local paths. The default artifacts are
`gem5/resources/x86-ubuntu-22.04-ros-humble.img` and
`gem5/resources/x86-linux-kernel-5.15.180`. KVM is used for boot only; measurement
uses Timing CPUs. The guest overlay is packaged with runtime libraries and
injected during boot into gem5's COW disk overlay. The original image remains
read-only; no offline mount or deployment is necessary.

Catch and miss are completed outcomes and return success at suite level.
Infrastructure failures return 2, stop subsequent expensive attempts and retain
logs. `--resume` retains completed trials and creates a fresh attempt for an
unfinished point. It rejects changed configuration, simulator, kernel, guest
bundle, runner/installed application artifacts or base-image metadata.
`COLCON_IGNORE` prevents staged packages from being rediscovered by colcon.

## Customize the experiment

[memory_contention.json](../ros/ball_catching_ws/experiments/memory_contention.json)
has fixed scene/coupling settings, one fixed hardware object, a Ramulator export,
named throws, named frequency tuples, patterns, pressure levels and calibration
settings. Relative Ramulator paths resolve beside the experiment JSON.

Each throw's position, direction and speed remain identical across rate/traffic
comparisons. The preset uses separate previously demonstrated 3 and 5 m/s
lofts: their geometry differs, so they do not establish a maximum speed for one
fixed launch vector. Native checks are estimates of feasibility, not guarantees
about the guest's performance. Change or add geometries only after screening.

The tuples initially use camera/planner/control rates of 60/15/125 Hz and
90/30/250 Hz. `plan_interval` explicitly sets the minimum interval between
admitted searches; a timer-rate change alone does not remove this limit. Joint
feedback remains fixed and must support the highest selected control rate.
Controller gains and physical limits stay fixed.

The default traffic is random, 80% reads, a 512-request bound and steady 100%
duty. Add `stream` or `stride` to compare patterns. Stream advances by a DRAM
transaction, stride advances by the configured aligned byte stride, and random
uses a deterministic seed. Physical bank/channel mapping makes pattern capacity
different; for example, a stride may repeatedly select one channel.

The device retains refused requests and stalls new generation when its pending
capacity is full. No requested memory access is silently dropped. It is a
bounded synthetic source, not a shader, warp scheduler, real GPU trace, shared
cache or coherence model. Traffic bypasses CPU caches and shares controller
queues, banks and timing with the guest. The External frontend uses source ID 0
for both streams; wrapper counters distinguish ownership, not upstream source
priorities. Read/write mixing follows blocks of 100 accepted requests.

For each pattern, a short aggressor-only run measures physically served DRAM
bandwidth at maximum injection rate. The initial estimate uses 100 us warmup
and 1 ms measurement. Pressure levels are offered GB/s divided by this calibrated
capacity: 0%, 75% and 125%. The last requests more work than the measured device
capacity; it does not imply 125% physical bus utilisation. Record offered,
accepted and physically served traffic separately. Calibration is configuration
and binary specific and is reused only when fingerprints match.

The aggressor starts at the detailed ROI and remains active through home/camera
readiness and a fixed 100 ms simulated warmup before launch. Every attempt
starts a new simulation from the fixed vertical home. The host scorer still
requires containment at two simulated seconds after launch, allowing escape
and recapture before that deadline. Neither readiness nor elapsed wall time
changes the task's evaluation duration.

## Measurements and report

Open `<output>/dashboard.html` locally. It combines configuration comparisons,
memory latency versus achieved aggressor bandwidth, binary outcomes, delivered
control cadence and the established recorded-motion replay. Filters select
throws and rate tuples; exports preserve run-level measurements. Pending,
interrupted and infrastructure-failed points remain visible and have no fabricated
task outcome. Native preflights have no guest memory measurements.

The memory window starts at the timing-server boundary after launch and ends at
evaluation. The start tick is retained in `gem5/measurement_start.json`; the
boundary delay is reflected in the actual measured duration. Compare that
duration with the task duration before interpreting a run. The suite does not
instrument application load-to-use latency:

- `accepted_*_ns`: guest DRAM-read acceptance to Ramulator completion.
- `total_*_ns`: first submission at the wrapper to that completion, including
  admission retries. This excludes waiting upstream in caches/fabric and later
  response delivery.
- Means use exact gem5 ticks. P95/P99 use completed-read counts in sparse 1 ns
  histograms, rounded upward. Reads first submitted before reset are excluded.
- Guest write acknowledgements are not DRAM-write completion measurements.
- Accepted aggressor GB/s counts accepted requests, including forwarding or
  coalescing. Physical bus GB/s counts served DRAM commands. Bus/peak is physical
  bandwidth divided by the fixed theoretical data-bus peak.
- Delivered control cadence uses host simulated-time receipts; it includes
  bridge/coupling effects and is not an internal controller deadline-miss count.

Guest per-update file logging is disabled to avoid adding its memory and I/O
work to this initial study. The replay comes from host physics records. Native
computation timings are available in native preflights; detailed guest execution
timings and consumed-frame attribution are not collected by this version.

The 1280×960 RGB pair carries 7,372,800 raw image bytes; at 90 Hz it represents
663.6 MB/s before serialization and copies. The generic ROS bridge transmits
opaque serialized messages. Its per-message limit is set from image dimensions;
the controller still has its existing 32 MiB aggregate queue bound. Queue
overflow fails explicitly. Local full-size stereo tests verify framing, payloads
and timestamps, but do not establish sustained guest throughput. Preserve any
overflow/freshness failure and consult before changing Chimaera buffering,
message selection, transport, or clock behavior. The existing m5 payload copy
is functional; it does not model a timed DMA transfer into DRAM. Subsequent guest
allocation, ROS copies and image processing execute on the simulated system.

Raw attempt directories retain the scene/URDF, result and launch timestamps,
host motion and command receipts, execution configuration, guest session,
coupling timing CSV, gem5 serial/config/statistics and Ramulator counters.

## Checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONPATH=ros/ball_catching_ws/src/ball_catching_sim:$PYTHONPATH \
  python3 -m pytest -q ros/ball_catching_ws/tests
python3 gem5/ext/ramulator2/tests/run.py \
  --case single --case vector --case aggressor-backpressure --case aggressor-drain \
  --output /tmp/ball-memory-tests
colcon --log-base ros/chimaera_ros_ws/log test \
  --base-paths ros/chimaera_ros_ws/src --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install --packages-select chimaera_ros_bridge
colcon --log-base ros/chimaera_ros_ws/log test-result \
  --test-result-base ros/chimaera_ros_ws/build --verbose
node --check ros/ball_catching_ws/dashboard/memory_comparison.js
```

Full simulation outcomes are separate from unit and transport checks.

### Local validation, 2026-10-12

The four native preset baselines each caught the ball: both speed geometries at
both frequency tuples, one throw each. Records and the comparison/replay report
are in `ros/ball_catching_ws/results/memory-native-suite-20261012`. This screening
does not establish guest feasibility or reliability.

The random-pattern calibration measured 21.897728 GB/s physically served over
the 1 ms window after warmup (42.769% of the 51.2 GB/s theoretical peak). It is a
short capacity estimate for the recorded preset and binary, not a universal
controller limit.

Validation passed 44 Python ball-catching workflow/report tests, 19 native
robot tests, five ROS bridge suites and three transport suites. The bridge
tests cover full stereo payloads at 640×480 and 1280×960 and clock floods in
both queues while preserving image FIFO. Memory regressions passed scalar,
vector, backpressure and drain cases, including forced guest admission retries
and measured latency tails. Dashboard controls passed a Node DOM smoke test;
visual browser verification is still outstanding.

The initial coupled attempt failed before launch because paused Gazebo flooded
the bridge's 16-message FIFO with unchanged clock timestamps. A standalone
diagnostic observed 5,096 clock messages over approximately ten wall seconds,
with only timestamps 0 and 0.5 ms. The approved `/clock` coalescing policy
addresses that queue. Full guest catch/miss results remain to be recorded;
native catches and short framing checks are not substitutes.
