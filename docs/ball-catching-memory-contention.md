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

The suite uses the existing keyed coalescing queues for `/clock` and stereo
images. Paused Gazebo publishes repeated clock timestamps in wall time; only its
newest pending update is retained. Images are matched by capture timestamp and
wrapped as one atomic pair before replacing the pending pair in both queues.
Unmatched arrivals are bounded to three images per camera. Joint and robot-output
routes retain FIFO behaviour.

The fixed `image_age_periods` budget is initially two camera periods: 22.222223 ms
at 90 Hz and 33.333334 ms at 60 Hz. The guest rejects expired pairs before ROS
allocation and again after copying. Perception checks age before detection and
before committing/publishing its estimate; results that expire during processing
are discarded. Frames already in flight cannot be replaced or recalled. Both
images must have the same timestamp; independently selecting each camera's latest
image would risk losing stereo correspondence.

Guest age checks use `m5_rpns()` relative to the exact detailed-simulation origin,
not delayed `/clock` callbacks or host wall time. After CPU switching, the suite
replaces its already-consumed boot readfile with that origin. The guest bridge
reads it after bootstrap confirms detailed execution and publishes a small local
origin file. An optional clock library lets perception read the same gem5 clock
without introducing simulator instructions into native execution. Native checks
use ROS simulation time. Image delivery fails closed until this clock is ready.
Readiness reflects capture age, so stale arrivals cannot falsely refresh it.

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
uses the fixed `hardware.cpu_type` (`timing` by default, or `o3`). The guest overlay is packaged with runtime libraries and
injected during boot into gem5's COW disk overlay. The original image remains
read-only; no offline mount or deployment is necessary.

Catch and miss are completed outcomes and return success at suite level.
Infrastructure failures return 2, stop subsequent expensive attempts and retain
logs. `--resume` retains completed trials and creates a fresh attempt for an
unfinished point. It rejects changed configuration, simulator, kernel, guest
bundle, runner/installed application artifacts or base-image metadata.
`COLCON_IGNORE` prevents staged packages from being rediscovered by colcon.

## Timing versus O3 comparison

This comparison isolates CPU execution behaviour before adding an aggressor.
Both runs use `case-003`: the fixed 3 m/s loft, 90/30/250 Hz camera/planner/control,
640×480 RGB stereo, a two-camera-period freshness limit, four 3 GHz cores,
identical caches and dual-channel DDR4. KVM still boots the same Ubuntu 22.04 / ROS
Humble image, then switches to the requested detailed model at workbegin. CPU
selection is included in configuration identity and resume checks. This is a
comparison of gem5 models, not a calibrated claim about a particular PC.

The O3 preset uses gem5's existing `X86O3CPU` defaults: eight-wide pipeline stages,
192 reorder-buffer entries and 32-entry load/store queues, with the default
functional units and branch predictor. The full resolved architecture is saved
in each attempt's `gem5/config.ini` and `gem5/config.json`. After switching,
`gem5/cpu_roi.json` records the actual active model, core count and switch tick;
the suite aborts if they differ from the requested model.

The x86 receive and readfile m5 instructions serialize younger instructions
until their functional guest-memory writes complete. Non-speculative execution
alone orders older instructions but lets O3 issue later loads early and retain
stale bytes. This ordering requirement applies to both the controller framing
buffer and the simulation-clock origin. Other m5 instructions retain their
existing flags; timestamp reads do not acquire the additional serialization.

To prepare both environments without running calibration, gem5 or Gazebo:

```bash
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --prepare-only --cpu-type o3 --case case-003 \
  --output ros/ball_catching_ws/results/cpu-comparison-640-fixed/o3
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --prepare-only --cpu-type timing --case case-003 \
  --output ros/ball_catching_ws/results/cpu-comparison-640-fixed/timing
```

Preparation checks the existing image, kernel and simulator, packages the guest
including its clock library, records artifact provenance and freezes the chosen
configuration in `prepared_config.json`. It creates `run.sh` with the ROS
environment, absolute artifact paths and exact selected case. No trials run
during preparation. The prepared local directories above can then be run from
the repository root, one at a time:

```bash
# First compare whether O3 can initialize and catch with fresh images.
bash ros/ball_catching_ws/results/cpu-comparison-640-fixed/o3/run.sh

# Run the matching Timing control separately when desired.
bash ros/ball_catching_ws/results/cpu-comparison-640-fixed/timing/run.sh

# Rebuild one offline dashboard after either or both runs.
python3 ros/ball_catching_ws/scripts/report.py \
  ros/ball_catching_ws/results/cpu-comparison-640-fixed/o3 \
  ros/ball_catching_ws/results/cpu-comparison-640-fixed/timing \
  --output ros/ball_catching_ws/results/cpu-comparison-640-fixed/dashboard.html
```

The run scripts resume completed work and retain interrupted attempts. Changed
runner, application, configuration or simulator artifacts are rejected; prepare
a fresh output after such changes. Combined reporting requires identical settings
and execution artifacts apart from CPU type. It retains separate trial IDs and
matches interference ratios to each model's own baseline. Failed startup remains
an infrastructure status, with no fabricated catch/miss or task-window latency.
Each attempt preserves `launch.log`, guest serial, `timing.csv`, `commands.csv`,
CPU configuration and DRAM statistics. Completed throws add `result.json` and
`benchmark.json` with the binary outcome and latency tails.

For a setup check that executes no guest instructions, the gem5 entry point also
accepts `--instantiate-only`. Pass the prepared `cpu_check.json` as `--experiment`
and the staged `guest.tar.gz` as `--bundle`, with the usual kernel/image arguments
and Chimaera config import path. It instantiates the full board at tick zero,
writes its resolved configuration and exits without opening the timing server
or calling the simulator. This validates construction, not CPU switching or
full-system execution.

For live progress, open another terminal in the repository root:

```bash
python3 ros/ball_catching_ws/scripts/watch_memory_suite.py \
  ros/ball_catching_ws/results/cpu-comparison-640-fixed/o3
```

This reads host records every five wall seconds and needs no ROS environment.
It shows the latest attempt, active CPU, simulated time, completed exchanges,
elapsed wall time, average simulation speed, last timing update and delivered
commands. Throw percentage starts only after `launch.json` exists; startup has
no known total duration or completion percentage. Catch/miss appears when scored.
`--once` prints a single snapshot; `--case` selects another case, or pass an
individual attempt directory. Ctrl+C stops the monitor without stopping the
benchmark. The monitor never contacts the guest or timing socket, writes no run
records and adds no simulated work. It tolerates partially written live records.
Fatal guest bridge errors and guest session exits are reported as infrastructure
failures even if the host runner continues advancing simulated time. That
advancement is not valid task progress; this observer does not stop the runner.
The runner independently checks the same explicit guest failures once per wall
second, stops the attempt and marks it as an infrastructure failure. It retains
logs, stops further cases and does not invent a catch/miss outcome. A scored
throw retains its outcome even if guest cleanup subsequently exits.
For guest diagnostics, tail the attempt's `gem5/board.pc.com_1.device`; host and
simulator output are in `launch.log`.

## Customize the experiment

[memory_contention.json](../ros/ball_catching_ws/experiments/memory_contention.json)
has fixed scene/coupling settings, one fixed hardware object, a Ramulator export,
named throws, named frequency tuples, patterns, pressure levels and calibration
settings. Relative Ramulator paths resolve beside the experiment JSON.
`hardware.cpu_type` accepts `timing` or `o3`; older files that omit it select
Timing. `--cpu-type` overrides it for an entire suite, keeping each traffic sweep
on one fixed architecture.

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

The preset uses 640×480 images with the same field of view and update rates.
An RGB pair carries 1,843,200 raw image bytes; at 90 Hz it represents
165.9 MB/s before serialization and copies, one quarter of the original
1280×960 image traffic. The generic ROS bridge transmits
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
python3 gem5/tests/chimaera/m5_write_ordering/run.py \
  --output /tmp/chimaera-m5-write-ordering
colcon --log-base ros/chimaera_ros_ws/log test \
  --base-paths ros/chimaera_ros_ws/src --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install --packages-select chimaera_ros_bridge
colcon --log-base ros/chimaera_ros_ws/log test-result \
  --test-result-base ros/chimaera_ros_ws/build --verbose
node --check ros/ball_catching_ws/dashboard/memory_comparison.js
```

Full simulation outcomes are separate from unit and transport checks.

### Local validation, 2026-10-12

The four original 1280×960 native baselines each caught the ball: both speed
geometries at both frequency tuples, one throw each. Records and the comparison/replay report
are in `ros/ball_catching_ws/results/memory-native-suite-20261012`. This screening
does not establish guest feasibility or reliability.

After reducing the preset to 640×480, all four native baselines also caught the
ball, one throw each, with unchanged launch geometry, field of view, physics and
frequency tuples. Records and the report are in
`ros/ball_catching_ws/results/memory-native-640-20261012`. The 44 Python workflow
and report checks passed with the reduced preset. A fresh coupled 90 Hz baseline
is required to establish whether the guest can sustain image ingress.

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
addresses that queue. After enabling it, the no-aggressor baseline advanced
133 boundaries (66.5 ms) and received a full 7.4 MB stereo batch, then hit the
controller's existing 32 MiB queue bound as subsequent images accumulated.
The ball had not launched, so this is an infrastructure failure, not a miss.
Diagnostic initialization statistics recorded 475,596 guest DRAM reads, a
41.754 ns mean, 71/86 ns P95/P99 and 0.715 GB/s physically served bandwidth.
These are prelaunch diagnostic numbers, not task-window measurements. The
records are in `results/memory-suite-clock-validation-20261012`. The user approved
trying reduced resolution first, so the preset now uses 640×480.

The 640×480 FIFO baseline still accumulated images. It was stopped before launch
after roughly 0.19 simulated seconds (about fourteen wall minutes), when received
batches had grown through 1.8, 3.7, 6.5 and 10.2 MB. The latest control command was
108 ms old on arrival. It did not reach the queue bound before interruption and
has no catch/miss outcome. Initialization diagnostics recorded 1,833,193 guest
reads, a 46.364 ns mean, 82/315 ns P95/P99, 1.023 GB/s physically served bandwidth
and zero controller admission retries. Records are in
`results/memory-suite-640-20261012`.

The user then requested a mechanism to avoid stale images and selected the
two-camera-period budget documented above. Freshness validation passed 48 Python
workflow/report checks, 22 native test results and six ROS bridge suites. It covers
atomic replacement of complete pairs, both full RGB resolutions, mismatched and
missing-camera arrivals, expiry before and during ROS copying, unchanged control
FIFO, and a guest clock that advances despite delayed ROS clock callbacks.
All four fixed native throws also caught with freshness enabled; their records
and report are in `results/memory-native-freshness-640-20261012`.
Further changes beyond this approved policy require consultation.
Full guest catch/miss results remain to be recorded; native catches and short
framing checks are not substitutes.

The coupled 640×480 freshness check was interrupted at the user's request to
prepare an O3 comparison. It advanced 518 boundaries (0.259 simulated seconds,
about nineteen wall minutes), receiving twelve image batches between 1,845,424
and 1,853,564 bytes. Guest logs confirmed expiry rejection both in the bridge and
during perception processing, using the same authoritative clock origin as gem5.
There was no throw or task outcome. Initialization diagnostics recorded 1,973,699
guest reads, 48.481 ns mean and 84/322 ns P95/P99, with zero admission retries.
Records are in `results/memory-suite-freshness-640-20261012`; these statistics are
prelaunch diagnostics. The newest-pair mechanism bounded image accumulation but
did not establish that Timing CPUs could meet the freshness deadline.

CPU comparison setup passed 54 Python workflow/report checks, including preparing
without calibration or guest execution, resuming a prepared bundle, rejecting
CPU changes on resume, and preserving independent results and baseline ratios in
the combined dashboard. Both real full-system boards instantiated at tick zero:
four KVM boot cores plus four selected detailed cores, with matched experiment
settings and current execution fingerprints. Guest bundles include the simulation
clock library. Local environments and construction records are under
`results/cpu-comparison-640/{o3,timing}`. Neither benchmark was executed; CPU
switching, freshness under O3 and catch/miss outcomes await the user's runs.

The user's first O3 attempt exposed a controller framing failure during
initialization: `Guest bridge: trailing controller data`, followed by
`guest_bridge exited (1); stopping session`. The final bridge exchange was at
0.269983493 s, and the last of eighteen commands arrived at 0.2695 s. Guest robot
processes were then interrupted by their supervisor. Exchanges covering 0.3–0.4
simulated seconds took 67.51 wall seconds, versus 1,495 wall seconds for 0.1–0.2 s;
the apparent speedup followed the loss of robot work. O3 remained selected, and
each exchange still advanced 0.5 ms. No throw occurred and no task result exists.

The host runner did not detect this post-workbegin guest failure. The agent
stopped the invalid attempt, preserved its logs and diagnostic record, and marked
the case as an infrastructure failure. Records are in
`results/cpu-comparison-640/o3/case-003-attempt-001`. The stop required process
termination after the normal shutdown timeout; final Ramulator counters were
unavailable, so no complete memory benchmark was constructed. The monitor now
checks guest failure messages instead of displaying ordinary startup progress.
The framing decoder found bytes remaining after the batch's declared messages;
the cause was still unknown at the end of this audit. No transport change or new
simulation was performed during that inspection.

### O3 receive ordering repair, 2026-10-12

A small standalone SE regression reproduced the fault without booting Linux or
running the robot. An older load warmed an eight-byte buffer containing zero;
`m5_chimaera_recv` then wrote the correct big-endian controller header value three.
A subsequent independent load read the header. The unpatched Timing CPU passed
at tick 113,886, while O3 exited with status one at tick 119,547 despite the m5
receive log confirming the correct host bytes. Records are in
`results/cpu-comparison-640-fixed/ordering-regression/before`. The functional
memory write updated the cache but
could not replace an old value already held in a CPU register by a younger load.
This explains how the decoder could retain a zero message count while its buffer
held a nonempty batch and report trailing controller data.

The x86 decoder now adds `IsSerializeAfter` to `M5OP_CHIMAERA_RECV` and
`M5OP_READ_FILE`, preserving `IsNonSpeculative` for older-instruction ordering.
The latter is also used to load the simulation-clock origin into guest memory.
The simulator was rebuilt with Ramulator enabled. All eight committed SE probes
passed: receive/readfile, Timing/O3 and classic/Ruby MESI Two Level caches, each
simulating less than one microsecond. Their logs and machine-readable summary
are in `results/cpu-comparison-640-fixed/ordering-regression/after`.
The regression source is
[`m5_write_ordering`](../gem5/tests/chimaera/m5_write_ordering/README.md).

The host runner now checks guest serial failures both before launch and after
workbegin. Tests cover a host that remains alive after the guest fails, including
a failure with a launch record already present, and verify cleanup without task
scoring. All 60 ball-catching Python checks and three transport suites passed.
The socket-dependent transport checks require host Unix-socket access in a
restricted environment.

Fresh comparison environments are staged in
`results/cpu-comparison-640-fixed/{o3,timing}` with the rebuilt simulator and
runner fingerprints. Both full-system boards also instantiated at tick zero,
with four KVM boot cores and four selected detailed cores; no guest instructions
were executed by these configuration checks. The original failed logs remain
under the previous output.
Full ball-catching execution, freshness deadlines under O3 and binary task
results still await the user's run; these isolated checks do not establish them.
