# Ball-catching memory interference suite

This local suite holds the computer, physics, cup, camera geometry and guest
software fixed while sweeping synthetic GPU-like DRAM traffic. Each configuration
produces one binary catch/miss result. It is a coarse demonstration of the task's
operating boundary, with no statistical reliability claim. Container execution
is a later step; configuration, per-attempt records and the report remain
independent of the local execution backend.

Docker preparation and isolated parallel workers are now available through the
[ball-catching container workflow](../containers/ball-catching/README.md). Its
[pattern preset](../ros/ball_catching_ws/experiments/memory_contention_patterns.json)
uses the qualified single 3 m/s throw with one shared no-aggressor baseline and
stream/stride/random traffic at 50%, 80% and 90% of per-pattern calibration.
Preparation does not run robot benchmark cases.

Local Gazebo, its ROS bridge and the Chimaera host bridge use `IGN_IP=127.0.0.1`.
This restricts discovery to loopback and prevents unrelated Docker network
removal from invalidating discovery sockets during a long run. The native
application exposes `transport_ip` for deployments that need another interface;
the current local benchmark fixes it to loopback.

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

Guest age checks use `m5_rpns()` relative to the fixed coupling origin,
not delayed `/clock` callbacks or host wall time. At workbegin, the suite
replaces its already-consumed boot readfile with that origin. The guest bridge
reads it after bootstrap establishes the timing session and publishes a small local
origin file. An optional clock library lets perception read the same gem5 clock
without introducing simulator operations into native execution. The ball-catching
guest uses address-based m5 operations and `libchimaera_gem5_clock_addr.so`, which
work under both KVM and the detailed CPU. Native checks
use ROS simulation time. Image delivery fails closed until this clock is ready.
Readiness reflects capture age, so stale arrivals cannot falsely refresh it.

The CPU reference remains four x86 Timing CPUs at 3 GHz, private 32 KiB
instruction/data L1 caches and an 8 MiB shared Ruby MESI last-level cache.
The default memory interface now approximates the Jetson AGX Orin 64 GB:
256-bit LPDDR5 at 6400 MT/s, with a theoretical 204.8 GB/s data-bus peak
([NVIDIA specifications](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/)).
The pinned Ramulator LPDDR5-6400 JEDEC preset models eight ganged 32-bit
channels, two x16 devices per rank, two ranks per channel, FRFCFS scheduling,
open rows, all-bank refresh and WCK synchronization. This organization provides
64 GiB modeled capacity and 64-byte BL16 transactions, matching the gem5
cache-line wrapper. The checked-in Python generator exports every timing
constraint; it does not reuse DDR4 timings. This channel/rank organization is
an approximation, not a claim about NVIDIA's physical wiring or proprietary MC.
The CPU, NVIDIA fabric/QoS and power management are not Orin models.
X86Board still exposes 3 GiB to the existing Ubuntu guest; the aggressor uses a
disjoint 256 MiB window above it. A memory-interface change requires a fresh
suite and calibration; DDR4 results remain a separate historical baseline.

The default scoring duration is 1.5 simulated seconds after launch. Startup
and readiness precede that window. Extra continuous-ready warmup defaults to
zero; `fixed.warmup` can add a hold if an experiment needs one. The separate
twelve-hour wall-time limit is unchanged. Both duration and the Ramulator configuration remain
customizable in the experiment JSON.

KVM now ends at the guest bridge's workbegin marker. The selected detailed CPU
(`timing` by default) executes arm/camera readiness with the aggressor disabled.
Immediately before applying launch pose and velocity, the host sends
`BENCHMARK_BEGIN` to the paused timing server. The server verifies the active CPU,
resets statistics and saves `gem5/measurement_start.json` before replying. There
is no CPU switch at launch. The clock origin stays fixed throughout readiness
and measurement, and statistics reset synchronously with the throw boundary.

Orchestrated experiments perform this readiness run only on the preparation
machine, once per compatible hardware/camera/controller configuration. The guest
bridge finishes an exchange, marks its next exchange for a fresh host handshake,
and requests a gem5 checkpoint. The final application image carries that checkpoint,
the exact backing disk, kernel and guest bundle, plus the host readiness input
replay. Each case restores without constructing any KVM CPU or KvmVM object.
Its host replays the readiness prefix while gem5 stays paused and checks the
resulting simulation time and joint positions/velocities against the saved boundary.
Gazebo's renderer/physics engine internals are reconstructed by replay, rather
than serialized as process memory. A mismatch fails the case.

The MESI checkpoint path copies drained dirty cache owners into physical RAM
and restores with empty caches. This avoids MESI's unsupported flush operation
and unreliable warm-cache trace reconstruction. Guest architectural state and
RAM survive; the short case warmup also repopulates caches. This policy is the
same for every case, including baselines.

Case pressure starts after restoration. `fixed.pressure_warmup` defaults to
50 simulated ms, is limited to one second, and rounds up to complete coupling
intervals. Measurement excludes this warmup and starts at launch. Readiness has
already passed without aggression; pressure does not extend the hold by requiring
readiness again. Baselines receive the same hold with no traffic. Ball launch
position, direction and speed are host-only and may vary across cases sharing a
checkpoint. Missing or changed checkpoints fail closed, with no boot fallback.

Use fresh suites for this protocol and validate full-system restore/throw outcomes
before claiming numerical equivalence to earlier runs. Native launches leave the
timing socket unset. Rebuild gem5, transport, `chimaera_ros_bridge` and
`ball_catching_sim`, and prepare new images/checkpoints after updating.

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
`gem5/resources/x86-linux-kernel-5.15.180`. Standalone runs use KVM only for boot;
readiness and measurement use the fixed `hardware.cpu_type` (`timing` by default,
or `o3`). Orchestrated cases restore readiness checkpoints without KVM.
The guest overlay is packaged with runtime libraries and
injected during boot into gem5's COW disk overlay. The original image remains
read-only; no offline mount or deployment is necessary.

Before a coupled run, the suite checks that the three fixed paths
`/tmp/chimaera_{g2h,h2g,time}.sock` are free. Occupied paths stop the invocation
before calibration or guest boot. Preparation, plan inspection and memory-only
calibration do not require these paths. The suite does not remove endpoints;
after a forced shutdown, check ownership with `ss -xapn` before clearing stale
files. The host controller also checks the actual bind result during construction
and reports a startup failure immediately.

Catch and miss are completed outcomes and return success at suite level.
Infrastructure failures return 2, stop subsequent expensive attempts and retain
logs. `--resume` retains completed trials and creates a fresh attempt for an
unfinished point. It rejects changed configuration, simulator, kernel, guest
bundle, runner/installed application artifacts or base-image metadata.
`COLCON_IGNORE` prevents staged packages from being rediscovered by colcon.

## Single Timing validation

The current validation preset is
[memory_contention_timing_validation.json](../ros/ball_catching_ws/experiments/memory_contention_timing_validation.json).
It contains one disabled-aggressor 3 m/s loft (`case-000`), 480×360 stereo at
30 Hz, the existing 30 Hz planner/250 Hz effort controller, four 3 GHz Timing
cores, and the fixed caches/Orin-like LPDDR5/coupling settings. The image age limit
remains two camera periods (66.667 ms). Guest DDS uses an 8 MiB shared-memory
segment and explicit UDP loopback transport. The robot/perception algorithms are
unchanged. Diagnostics are enabled for validation and are frozen in provenance;
future comparison suites must keep their diagnostic setting consistent.

Prepare after building and sourcing both overlays:

```bash
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --prepare-only --cpu-type timing --case case-000 \
  --config ros/ball_catching_ws/experiments/memory_contention_timing_validation.json \
  --output ros/ball_catching_ws/results/timing-validation-orin-warmup0-1p5
```

The prepared run executes 1.5 simulated seconds after launch, with a twelve-hour
wall limit covering boot, readiness and the throw. Run from the repository root:

```bash
bash ros/ball_catching_ws/results/timing-validation-orin-warmup0-1p5/run.sh
```

Track progress in another terminal; this requires no ROS setup:

```bash
python3 ros/ball_catching_ws/scripts/watch_memory_suite.py \
  ros/ball_catching_ws/results/timing-validation-orin-warmup0-1p5 --case case-000
```

The monitor reports simulated time, separate arm/fresh-camera readiness, sampled
image ages, throw progress and catch/miss. Ctrl+C stops the monitor only. A completed
suite saves `dashboard.html` and the attempt's binary `result.json` and DRAM
`benchmark.json`; an unlaunched timeout remains an infrastructure failure.
Preparation executes no gem5 or Gazebo simulation. The full guest outcome awaits
the user's run; native qualification is a feasibility check.

Current presets allow twelve wall hours per attempt. The bridge's separate
15-minute startup deadline covers connecting to gem5 and local ROS routes; it
does not cap robot readiness during coupled KVM startup. Running processes
load their limits at startup. Existing prepared directories keep their frozen
budgets; an increased limit requires a fresh directory and a restarted run.
The original `timing-validation-480-dds8` directory therefore retains its
four-hour limit. Stop its running invocation before starting the twelve-hour
replacement above.

## Coarse interference demonstration

[memory_contention_demo.json](../ros/ball_catching_ws/experiments/memory_contention_demo.json)
uses the qualified 480×360, 30/30/250 Hz Timing configuration with the Orin-like
LPDDR5 interface, zero extra ready hold and a 1.5-second scoring window. Only
aggressor pressure changes across its three cases:

| Case | Offered aggressor demand |
| --- | --- |
| `case-000` | Disabled |
| `case-001` | 75% of calibrated sustainable bandwidth |
| `case-002` | 90% of calibrated sustainable bandwidth |

Each case has one fixed 3 m/s throw and one binary outcome. The random traffic
uses 80% reads, a fixed seed, bounded pending work and admission backpressure;
unaccepted work waits. The percentages describe offered demand relative to the
aggressor-only calibration, not physical bus occupancy. The previous calibration
measured 50.453568 GB/s, giving approximately 37.84 and 45.41 GB/s demand for the
two pressure points. Fresh suites calibrate before running; the continuation
below reuses the verified saved calibration.
The dashboard records actual served bandwidth, admission stalls and latency.

The steady aggressor runs from the detailed-simulation origin, including robot
readiness. A failure to reach readiness remains an infrastructure outcome,
separate from a launched throw's catch/miss. All hardware, geometry, rates,
diagnostics, coupling and readiness settings stay fixed across the three cases.
The first case supplies a matching zero-hold baseline; the historical 100 ms
hold validations cannot replace it. A separate zero-hold validation is not
needed before this demonstration.

Prepare after sourcing both overlays; preparation runs no simulation:

```bash
/usr/bin/python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --prepare-only --cpu-type timing \
  --config ros/ball_catching_ws/experiments/memory_contention_demo.json \
  --output ros/ball_catching_ws/results/memory-orin-demo-warmup0-90
```

Run from the repository root, then monitor in another terminal:

```bash
bash ros/ball_catching_ws/results/memory-orin-demo-warmup0-90/run.sh

/usr/bin/python3 ros/ball_catching_ws/scripts/watch_memory_suite.py \
  ros/ball_catching_ws/results/memory-orin-demo-warmup0-90 --case case-002
```

The monitor follows the active case automatically, then the latest attempt.
Use `--case case-001` to inspect a specific case. The runner executes cases
serially, saves the dashboard after every outcome and resumes completed cases
when the same `run.sh` is invoked again. Each attempt has its own twelve-hour
wall limit. Infrastructure failures stop the suite for inspection.

The prepared local `memory-orin-demo-warmup0-90` continuation imports the completed
baseline and recovered 75% throw with their original provenance. Its `run.sh`
selects only `case-002` at 90%. The original suite retains its frozen configuration
and archived reporting failure; its unrun 125% point is marked skipped at the
user's request. Use the continuation's launcher to proceed. The two completed
throws and the memory-only calibration are not repeated.

The host verifies the initial free flight on the first physics step after launch,
using the requested pose/velocity and world gravity. Tolerances are 2 mm in
position and 0.02 m/s in velocity. `launch_validation.json` records the requested
and observed state. A mismatch pauses physics and produces no task score; the
suite stops, the monitor reports a launch failure, and reporting rejects an
invalid throw. These privileged checks remain on the host. They add no guest
inputs and keep their settings fixed across interference points.

## Timing versus O3 comparison

This comparison isolates CPU execution behaviour before adding an aggressor.
Both runs use `case-003`: the fixed 3 m/s loft, 90/30/250 Hz camera/planner/control,
640×480 RGB stereo, a two-camera-period freshness limit, four 3 GHz cores,
identical caches and dual-channel DDR4. KVM still boots the same Ubuntu 22.04 / ROS
Humble image, then switches to the requested detailed model at the throw boundary. CPU
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

### Timing recovery diagnostics

Current recovery work uses Timing CPUs. O3 remains an optional model, but a
working Timing baseline must launch, score a throw and save its DRAM statistics
before interference comparisons are meaningful. Use a fresh suite after rebuilding
runner or application artifacts; previous attempts keep their original provenance.

Set `fixed.image_diagnostics` to `true` in a short diagnostic configuration to
emit `CHIMAERA_IMAGE` records in guest serial. Bridge stages report receipt,
serialized-message copying and ROS publication. Perception stages report input,
stereo pairing, conversion, detection, estimation and publication. Each record
contains capture time, authoritative current simulated time, age and budget in
nanoseconds. The first three samples per stage and subsequent samples at least
100 ms apart are logged. Samples from different stages may refer to different
frames; compare capture timestamps before subtracting stage times.

Diagnostics are disabled by default and their flag is frozen in suite identity.
Enabled diagnostic runs add logging and clock reads; use them to locate stalls,
then disable them for task and memory comparisons. Native `run_trial.py` accepts
`--image-diagnostics` and uses the native ROS simulation clock.

The host saves `readiness.csv` with separate arm/perception flags and the elapsed
continuous-ready warmup. The monitor displays these flags and sampled image ages,
so advancing physics alone is no longer the only visible progress signal. These
records do not change the launch condition or bypass stale-image rejection.

## Customize the experiment

[memory_contention.json](../ros/ball_catching_ws/experiments/memory_contention.json)
has fixed scene/coupling settings, one fixed hardware object, a Ramulator export,
named throws, named frequency tuples, patterns, pressure levels and calibration
settings. Relative Ramulator paths resolve beside the experiment JSON.
`hardware.cpu_type` accepts `timing` or `o3`; older files that omit it select
Timing. `--cpu-type` overrides it for an entire suite, keeping each traffic sweep
on one fixed architecture.

`fixed.guest_dds_shm_bytes` sets each guest Fast DDS participant's shared-memory
segment. The presets use 8 MiB; older files that omit it select `0`, preserving
default middleware transport. A nonzero value must fit one stereo pair plus
64 KiB and be at most 64 MiB. It generates `dds.xml` in the guest bundle, pins
guest middleware to `rmw_fastrtps_cpp`, and supplies the profile to both bridge and
robot processes. The bundle includes the dynamically loaded middleware plugin
and its Fast DDS dependencies, fixing their versions to the native installation
used for transport checks. The explicit profile restricts UDP to `127.0.0.1` and retains
one shared-memory transport. Capacity uses the existing guest RAM and remains
fixed across traffic points. Coalescing, ROS queue depths, image-age checks and
the Chimaera transfer protocol keep their existing behaviour.

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
readiness. The default extra warmup is zero: launch can occur at the first
physics step with both flags true. Robot readiness still requires the fixed
home tracking tolerances and low joint speeds; perception readiness still
requires a fresh stereo pair. Every attempt
starts a new simulation from the fixed vertical home. The host scorer still
requires containment at 1.5 simulated seconds after launch, allowing escape
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

Guest per-update computation timings are now exported through the small,
reliable `/benchmark/{perception,interception,control,control_timing}` ROS routes. They use `m5_rpns()` relative to the
same detailed-simulation origin as image freshness, with
`*_simulation_seconds` fields and `timing_clock: "gem5 simulation"`. This
clock advances during computation even if a ROS `/clock` callback is delayed.
The host persists envelopes in `guest_records.jsonl`; after shutdown the runner
checks per-stream sequence continuity and writes `perception.jsonl`,
`interception.jsonl`, `control_timing.jsonl` and `control.jsonl`. Missing
perception/control telemetry or a sequence gap is an infrastructure error.
The report also matches exported controller timings against independently
received effort-command timestamps, so an undelivered contiguous tail is shown
as a partial timing export.
No guest file logging or serial-console transfer is enabled. Clock reads,
JSON serialization and DDS export add fixed instrumentation overhead to every
case; export follows the measured computation but affects later work and DRAM
traffic. A controller update is measured once, before either timing export.

Dashboard comparison rows include mean/P95/P99 computation in milliseconds,
image age, host wall duration and delivered control cadence. Detailed timing
rows show the clock, counts and the perception substeps, admitted planning
searches (including failed searches), controller updates and capture-to-completion
age. Expired perception work is recorded with `status: "expired_processing"`
and the stages actually attempted. Records completed after scoring are
excluded; records still in flight at shutdown are not counted. Sequence checks
cannot detect an undelivered final tail. Native preflights retain steady-clock
`*_wall_seconds` measurements and are labeled separately. Old guest runs did
not collect these records and still show unrecorded timings; they cannot be
recovered as full per-update distributions from sampled diagnostics.
Empty exported streams represent recorded zero counts.

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
ctest --test-dir ros/ball_catching_ws/build/ball_catching_sim --output-on-failure
```

Full simulation outcomes are separate from unit and transport checks.

The simulation package's `launch_stepping` test checks real Fortress continuous,
batched and individually paused physics updates without booting gem5 or running
robot computation. An injected velocity controller also checks that a broken
launch stops before scoring. Source and diagnostics are in
[`launch_stepping.py`](../ros/ball_catching_ws/src/ball_catching_sim/test/launch_stepping.py)
and `build/ball_catching_sim/launch-stepping-validation/`.

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

### Stale-socket startup repair, 2026-10-12

The next two user attempts failed during KVM boot, before workbegin or the O3
switch. Both aborted in the first `chimaeraSend` with
`src/chimaera/util.cc:47: panic: connect: Connection refused`. The earlier forced
shutdown had left unowned `chimaera_g2h.sock` and `chimaera_time.sock` files.
The host's G2H bind failed on that stale path, but its controller stored the
error in the I/O worker and waited for the timing server. The guest then tried
to connect to a socket file without a listener. These attempts produced no
coupling intervals, ball launch or task outcome and did not exercise O3.

After checking the host's Unix-socket table and process list, the agent removed
only those two unowned socket files. A suite preflight now rejects occupied
transport or timing paths before expensive execution. The transport exposes an
immutable initialization result, and the host controller throws immediately if
either listener could not bind. Existing socket files are preserved, including
when the other endpoint has already opened successfully. The runner and monitor
also surface explicit simulator panics and host bridge errors from `launch.log`.

Validation passed 66 ball-catching Python checks, three transport suites and all
six ROS bridge suites. The checks cover both active and stale G2H/H2G sockets.
Four probes against the actual rebuilt Gazebo host executable reported the bind
error within their five-second test limit despite a 900-second startup timeout;
these probes use neither gem5 nor Gazebo. Existing short native physics checks
also passed. The gem5 binary and CPU configuration were unchanged by this repair;
no full guest benchmark was run by the agent.

The failed setup and both attempts are archived under
`results/cpu-comparison-640-fixed/previous-setup/{o3,timing}`. Fresh matched bundles
and manifests use the same `results/cpu-comparison-640-fixed/{o3,timing}/run.sh`
launcher paths, with current runner and installed bridge fingerprints.

### Timing baseline recovery, 2026-10-13

Recovery uses four Timing CPUs with the existing fixed hardware and disabled
aggressor. Local Gazebo discovery now explicitly binds to `127.0.0.1`; it no
longer depends on a Docker bridge remaining present. The native 640×480,
90/30/250 Hz trial caught the fixed 3 m/s loft even with an invalid inherited
`IGN_IP`. Image-stage diagnostics and separate arm/camera readiness records
are available as described above. Validation passed 68 ball-catching Python
checks, all six bridge suites, and robot/scoring CTest suites.

Native screening kept the launch geometry, physics, joint feedback, planner and
effort controller fixed. Reducing only resolution to 320×240 caused a miss;
the first visual detection occurred about 371 ms after launch. That scene is
not eligible as the interference baseline. Keeping 640×480 and reducing only
camera rate to 30 Hz caught the same throw natively. Native catches establish
geometry feasibility, not a working detailed guest run.

At 640×480 and 15 Hz, the native trial also missed. A 264 ms gap in visual
observations reset estimator initialization; the first state estimate arrived
too late for a feasible intercept. That frequency is not eligible as the
baseline for this geometry. Keeping 30 Hz and reducing only resolution to
480×360 caught the fixed throw, with the first estimate captured 163 ms after
launch and the first plan at 199.5 ms after launch. This is the next qualified
native candidate, not yet a validated detailed guest baseline.

The bounded 640×480, 30 Hz Timing diagnostic exposed two distinct delays. For
the pair captured at 66.5 ms, bridge receipt/publication occurred at
77.414/85.249 ms; perception paired it at 88.658 ms and finished detection at
139.987 ms. Its final age was 73.487 ms, exceeding the two-period 66.667 ms
budget, so the result was rejected. The next pair took another 48.280 ms from
conversion to completed detection and finished with a 93.925 ms age; it was
also rejected. These two initial samples do not establish steady-state detector
cost. The arm became ready while camera readiness remained false. The diagnostic
reached 196.5 ms of simulated time before its 900-second wall limit, with no
launch or task result. Cleanup released all three Chimaera sockets without
manual removal.

A separate native DDS probe used the installed Fast DDS 2.6.12 and
rmw_fastrtps 6.2.10, sending three stereo pairs of 921,600-byte RGB images. With
the receiver process paused for 100 ms around each publication, default DDS
delivered zero of six images. An explicit loopback-only profile with an 8 MiB
shared-memory segment delivered all six, both with and without the pause.
The default unpaused check delivered four of six. Humble's
`ROS_LOCALHOST_ONLY=1` adds a default shared-memory transport even when an XML
profile is supplied; the successful profile therefore used its own UDP loopback
whitelist with that flag disabled. The guest DDS configuration was subsequently
approved and is now available as the fixed `guest_dds_shm_bytes` setting. This isolated
test reproduces loss under a delayed receiver; it does not attribute every
missing guest frame to DDS.

Raw records, the DDS probe source/profile, and diagnostic manifests are under
`ros/ball_catching_ws/results/timing-recovery-20261013`. Bounded diagnostics that
end without launch/score are infrastructure checks, not catch/miss results.
A full guest throw and its DRAM measurement window remain to be validated.

### Guest DDS preparation, 2026-10-13

The approved profile is now generated by the suite and included in the guest
bundle. A missing profile fails before any guest children start. Existing session
manifests retain localhost mode; this suite explicitly selects its loopback-only
transports so Humble does not append the default SHM transport.

Validation passed 77 ball-catching Python checks and all six ROS bridge suites.
The production profile generator delivered all six 640×480 test images with
the receiver running and all six with the receiver briefly paused. The native
480×360, 30 Hz trial using that profile caught the fixed 3 m/s loft. These records
are in `results/timing-recovery-20261013/dds-generated-profile-check` and
`results/timing-recovery-20261013/native-480-camera30-dds8`.

The single Timing baseline is prepared under
`results/timing-validation-480-dds8`; preparation runs no simulator. The full
guest validation is delegated to the user with the launch and monitoring commands
above. Its outcome and memory measurements remain pending.

### Paused launch repair, 2026-10-13

The twelve-hour Timing attempt completed its two-second throw after about
2 h 56 min of wall time. At launch (0.5285 s), the ball had the requested
velocity: approximately (-1.342, 0, 2.678) m/s after gravity for one physics step.
One millisecond later its velocity was (0, 0, -0.00981) m/s and its x position
had returned to 1.8 m. It fell vertically outside the arm's interception
workspace. The guest delivered 444 effort commands, and sampled image results
were about 40 ms old, within their 66.667 ms budget. The raw scorer reported
`outside_cup_at_deadline`; this is an invalid launch, not evidence of the robot's
memory-interference boundary.

Fortress applies physics commands during paused callbacks even though it skips
integrating time; its velocity-command component remains present with zero data
after the launch step. See the upstream
[Physics implementation](https://github.com/gazebosim/gz-sim/blob/ign-gazebo6/src/systems/physics/Physics.cc).
The host's cleanup previously returned early on every paused callback. It now
removes both launch command components before that return. Continuous and
batched execution had masked the issue because cleanup ran before the next
physics update.

A short native regression reproduced the velocity reset with individually
paused steps and passed with continuous and batched execution before the fix.
All three modes preserve the requested ballistic trajectory after the fix.
An injected velocity override is rejected within one further 0.5 ms physics
step and produces no catch/miss score. Both simulation CTest suites and all
84 ball-catching Python checks passed. The unchanged native 480×360, 30 Hz
throw caught the ball with a verified launch after the repair.

The old attempt remains under `results/timing-validation-480-dds8-12h`, including
its raw scorer, DRAM records and logs. An offline launch-validation record marks
it invalid; its suite/dashboard now show an infrastructure failure and retain
the attempt name in `invalid_trials`. The previous manifest is saved alongside
it. Regression records are under `results/timing-recovery-20261013/launch-stepping-before`,
`build/ball_catching_sim/launch-stepping-validation`,
`results/timing-recovery-20261013/launch-stepping-after`, and
`results/timing-recovery-20261013/native-480-camera30-launch-fixed`.

A fresh environment is prepared in `results/timing-validation-launch-fixed`
with the same Timing hardware, camera/freshness settings, disabled aggressor and
twelve-hour wall budget. Full guest execution after this repair awaits the user;
the agent has not started that run.

### Successful Timing baseline, 2026-10-13

The user's run of `results/timing-validation-launch-fixed/case-000-attempt-001`
completed with a verified ballistic launch and **CATCH**. The actual detailed
cores were four `BaseTimingSimpleCPU` instances, matching the frozen 3 GHz
architecture. The fixed 3 m/s throw launched at 0.529 s, continuous final
containment began at 1.497 s, and evaluation at 2.529 s found the ball still
inside the cup: 1.032 s of continuous containment. Independent quaternion-based
conversion of the final motion record also passed the polygonal cavity check.
The cup moved about 24.2 cm from launch to the start of final containment.

The saved benchmark matches a fresh calculation from raw gem5/Ramulator data:

| Guest DRAM read metric | Cost |
| --- | ---: |
| Mean | 55.247647 ns |
| P95 | 85 ns |
| P99 | 337 ns |

There were 2,382,945 completed guest read samples and zero admission retries.
Accepted and total wrapper latency therefore agree. The measured window was
2.0005 s, including 0.5 ms beyond the task deadline during shutdown; this is
0.025% of the task window and remains visible in `benchmark.json`. The aggressor
accepted no requests. Physically served reads/writes used 0.140272 GB/s,
about 0.274% of the fixed 51.2 GB/s bus peak.

During the task, 380 distinct effort receipts gave an effective 190.93 Hz
delivery cadence against the configured 250 Hz controller timer; the p95 gap
was 12 ms. This measures host receipts through the coupling, not internal guest
deadline misses. Sampled completed detections were 33.8–48.3 ms old and sampled
published estimates were 34.3–37.3 ms old, within the 66.667 ms freshness budget.
No freshness warning or queue overflow was logged. These sampled records do not
establish complete frame counts. The arm first reported ready at 0.429 s and
the camera at 0.117 s; the fixed 100 ms warmup preceded launch.

Wall runtime including startup and cleanup was 11,875.774 s (3 h 17 min 56 s).
Boot recovered from one virtual-disk read timeout. Shutdown used SIGTERM for
remaining ROS/Gazebo children after their initial signal timeout; gem5 finished
cleanly, final memory records were saved, and all fixed sockets were released.
Neither event invalidated the scored throw or its measurements. Simulator,
kernel, guest bundle, runner/installed artifacts and base-image metadata matched
the saved execution provenance (`cb2591d`).

The evaluation is saved as `results/timing-validation-launch-fixed/evaluation.json`,
and the regenerated `dashboard.html` contains the catch, motion replay, memory
metrics and explicit unrecorded guest details. Offline report checks passed
eight tests and a DOM check using the actual dashboard data. This run qualifies
the 480×360, 30/30/250 Hz configuration for coarse interference comparisons.
Use its fixed geometry, diagnostics, hardware and coupling settings for the
next random-traffic pressure points, initially 75% and 125% of the cached
calibrated capacity. One successful disabled-aggressor throw is the baseline;
the interference failure boundary remains to be measured.

### Guest timings and LPDDR5 preset, 2026-10-13

The new defaults use the Orin-like LPDDR5 interface and a 1.5-second scoring
window. Guest records are exported to the host and the dashboard compares
mean/P95/P99 perception, planning and controller computation alongside DRAM
latency. The historical successful DDR4 run above retains its original
configuration and remains without per-update guest computation timings.

Validation passed 94 Python workflow/report tests, the robot core and runtime
CTest targets, the scoring target and the paused-launch regression. The runtime
test holds ROS time at zero while advancing a fake gem5 clock, checks the exact
processing duration and completion timestamp, confirms pre-ROI records are
excluded, and receives a sequence-zero telemetry record over local ROS.
A gem5 memory-only correctness/retry test passed with the new LPDDR5 preset,
random traffic, backpressure and 100 GB/s offered demand. An independent 100 us
warmup + 1 ms random-traffic calibration measured 50.453568 GB/s physical bus
traffic, versus 204.8 GB/s theoretical peak. It does not establish GPU application
throughput or validate the full robot guest.

Native checks retain their individual binary outcomes:

- `results/native-1p5-timing-telemetry`: MISS; no feasible plan was produced.
- `results/native-2s-timing-telemetry`: CATCH; continuous capture began 0.9685 s
  after launch, before either deadline.
- `results/native-1p5-timing-telemetry-repeat`: CATCH at the requested 1.5 s
  deadline, with 0.5315 s continuous containment.

The repeat's offline dashboard includes native wall-clock processing timings.
These coarse host checks demonstrate a viable shortened window and retain the
observed variability; they do not establish catch reliability. The full-system
LPDDR5 validation remains user-run in a fresh prepared suite. Its timing export
and catch/miss result must be checked before sweeping interference levels.


### LPDDR5 full guest evaluation and timing export tail, 2026-10-13

The user's `results/timing-validation-orin-timings-1p5/case-000-attempt-001`
completed with a verified ballistic launch and **CATCH** at the 1.5-second
scoring deadline. Launch was at 0.554 s, final continuous containment began at
1.5405 s (0.9865 s after launch), and evaluation at 2.054 s recorded 0.5135 s
of uninterrupted containment. An independent rotation of the last ball position
into the cup frame passed the polygonal cavity check. The cup moved about
18.7 cm before final continuous containment. The four actual detailed cores
were `BaseTimingSimpleCPU`; the frozen LPDDR5 configuration and disabled
aggressor matched the intended baseline. Runtime was 10,767.566 wall seconds,
about 2 h 59 min 28 s.

Raw gem5/Ramulator recalculation matched the saved memory metrics:

| Guest DRAM read metric | Cost |
| --- | ---: |
| Mean | 76.851855 ns |
| P95 | 132 ns |
| P99 | 331 ns |

There were 2,216,692 read samples, zero guest admission retries and zero
aggressor requests. Physically served reads/writes used 0.173787 GB/s, about
0.08486% of the 204.8 GB/s peak. The measurement window was 1.5005 s, with
0.5 ms beyond scoring during cleanup. During the task, 277 distinct effort
receipts gave an effective 184.987 Hz against the configured 250 Hz timer.
The p95 receipt gap was 12.5 ms.

The timing export is **partial**. Its sequences are contiguous, but the last
perception completion received was at 1.719223122 s and the last control timing
at 1.714017337 s; the robot and bridge continued until scoring at 2.054 s.
Sixty of the 277 independently delivered task effort commands have no matching
exported timing. Captured records include 35 task perception callbacks, seven
planning searches (three feasible), and 218 controller updates. Their reported
mean/P95/P99 durations are 26.623/27.002/27.638 ms for perception,
85.594/119.465/119.465 ms for planning and 0.200/0.239/0.356 ms for control.
These are distributions of the received subset, not complete task-window
computation statistics. Sampled image diagnostics continue after the export
stopped; none of those sampled ages exceed the 66.667 ms freshness budget.
No image overflow or expired-processing warning was logged.

A short local ROS probe reproduced the shared-topic bottleneck: one
`spin_some()` serviced one of 40 pending messages on the combined telemetry
topic, versus four messages when each stream had a separate topic. The
benchmark now gives each timing stream its own reliable FIFO route and host
subscriber. No Chimaera core or image-transport change is included. A runtime
regression checks that one bridge-style callback pass services every pending
stream. The offline report checks command/timing correspondence and marks this
completed run's export as partial, preserving its catch and valid DRAM results.
Queued or in-flight records may still remain at the final boundary; the check
measures delivered-command coverage and does not prove receipt of every
record generated by the guest.

Gem5 finished cleanly and wrote the final statistics. During shutdown, the
host bridge logged a Gazebo step mismatch after scoring and the stop signal;
remaining ROS/Gazebo processes needed SIGTERM after the normal signal timeout.
The saved scoring record, launch check and both memory snapshots are consistent;
this shutdown message does not invalidate those measurements. It is retained
in the evaluation, rather than described as a completely error-free exit.
Before applying the export correction, simulator/kernel/bundle hashes,
installed execution fingerprint and base-image metadata matched the manifest's
`4b67e6c` provenance.

The evaluation is saved in `results/timing-validation-orin-timings-1p5/evaluation.json`.
A fresh user-run Timing validation is prepared to check export coverage through
the deadline before expanding the memory-interference sweep.


### Remove the extra ready hold, 2026-10-13

Both default experiment presets now set `fixed.warmup` to zero, removing the
additional 100 ms of continuously ready simulation time before launch. The
existing host already supports zero; its home/low-joint-speed and fresh-camera
readiness gates remain in effect. This is a configuration change and requires
no rebuild or simulator change. The monitor distinguishes waiting for the arm
or camera from the extra ready hold.

In the completed LPDDR5 run above, both readiness flags first became true at
0.454 simulated seconds and launch occurred at 0.554 seconds. Summing the
corresponding `timing.csv` intervals gives 2,178.234 wall seconds (36 min 18 s)
to reach readiness and another 454.437 seconds (7 min 34 s) for the extra hold.
Removing that hold saves roughly 7.5 minutes at those observed speeds; this is
an estimate, not a timing guarantee for a new configuration. Readiness startup
still depends on simulated execution and the robot's settling dynamics.
The short 100 us aggressor-only calibration warmup is unchanged and is separate
from the expensive robot startup/hold.

The then-active `results/timing-validation-orin-timing-routes-1p5` run retains its
frozen 100 ms hold. Its prepared files, binaries, launch condition and execution
fingerprint are not changed. New zero-hold suites need a fresh output directory;
changing this setting between pressure points would weaken comparison with
the previous baseline. No expensive guest or native co-simulation is started
by this change.

### Separate timing routes validated, 2026-10-13

The user's `results/timing-validation-orin-timing-routes-1p5/case-000-attempt-001`
completed with a verified ballistic launch and **CATCH**. Its frozen configuration
retained the 100 ms extra ready hold; the separately prepared zero-hold validation
has no attempts. Four actual `BaseTimingSimpleCPU` cores, the LPDDR5 configuration,
simulator/kernel/bundle hashes, installed execution fingerprint and base-image
metadata matched its `32708b4` provenance. No aggressor requests were accepted.

Launch was at 0.5315 s and scoring at 2.0315 s. Final continuous capture began
0.968 s after launch, giving 0.532 s of containment. Independent quaternion-based
conversion of the final motion record passed the polygonal cup check. The cup
moved about 23.4 cm before final capture. Wall runtime was 10,712.000 seconds
(2 h 58 min 32 s).

Raw memory and delivered-command recalculations matched the saved benchmark.
There were 2,112,709 guest DRAM read samples, zero admission retries and a
1.5005-second measurement window, including 0.5 ms of cleanup beyond scoring.
Physically served read/write traffic was 0.164836 GB/s, about 0.08049% of the
204.8 GB/s interface peak. The task delivered 276 distinct effort commands at
185.061 Hz against its configured 250 Hz timer, with a 12 ms p95 receipt gap.

| Measurement | Mean | P95 | P99 | Samples |
| --- | ---: | ---: | ---: | ---: |
| Guest DRAM reads | 77.092 ns | 132 ns | 331 ns | 2,112,709 |
| Perception + estimation | 25.406 ms | 27.032 ms | 27.849 ms | 44 |
| Planning searches | 79.156 ms | 116.996 ms | 116.996 ms | 7 |
| Arm control | 0.239 ms | 0.310 ms | 0.358 ms | 275 |

Application costs use guest gem5 simulation time. These application distributions
describe the received subset: 275 of 276 delivered task commands have matching
control timings (99.64%), improving from 217/277 before the separate-topic fix.
One final command, stamped 2.013 s and received at 2.029 s, has no exported timing.
The dashboard retains its **partial** label; this check does not prove receipt of
every record generated by the guest. The last received control completion is
21.566 ms before scoring, versus the previous 339.983 ms tail. Perception's last
received completion is 16.638 ms before scoring. The large shared-topic backlog
is resolved. Planning stops earlier as part of the robot's approach/braking
behaviour; its tail is not treated as an export backlog.

All four exported stream sequences are contiguous. Of 145 valid sampled image
diagnostics, none exceeded the 66.667 ms freshness budget; the maximum observed
age was 50.607 ms. No image overflow, stale-result or expired-processing warning
was logged. These samples do not establish complete image coverage.

Boot recovered from two virtual-disk read timeouts. Gem5 finished cleanly and
wrote final statistics. The bridge logged a Gazebo step mismatch after scoring
and the stop signal, and remaining ROS/Gazebo children needed SIGTERM after the
five-second shutdown grace period. Those retained shutdown warnings do not
invalidate the consistent launch, score and memory records. All fixed sockets
were released.

The checked evaluation and regenerated report are saved as
`results/timing-validation-orin-timing-routes-1p5/evaluation.json` and
`dashboard.html`. The actual-data dashboard DOM check passes, including the
partial-coverage warning and DRAM/application timings. The coarse three-case
demo above is prepared next; its new matching baseline removes the extra hold.
The agent has not started a full guest run for that demo. This single catch
qualifies the configuration for a coarse comparison; the interference failure
boundary remains to be observed.

### Scientific latency labels recovered and 90% continuation, 2026-10-14

The user's first two zero-hold demo throws both produced **CATCH**, verified
ballistic launches and 0.532 s of final containment. Startup reached readiness
and launched without the extra 100 ms hold. The 75% case completed simulation,
then its reporting step failed with `cpuReadTotalLatencyNs sample count mismatch`.
The suite stopped before the originally planned 125% case. Both scored outcomes
and their raw guest/memory records remained on disk.

The exact failure was one guest read's long admission-inclusive latency bucket,
printed by gem5 as `3.74686e+06` ns. The previous parser accepted only digits in
bucket labels, so it omitted that one sample: 1,850,751 parsed bins versus
1,850,752 reads. Gem5 had exported the sample correctly; the old suggestion to
rebuild the simulator was misleading. The parser now accepts scientific and
decimal integer labels, sums repeated rounded labels directly from the final
statistics block, and still rejects invalid bins or genuine count mismatches.
The mean remains calculated from the independent tick sum.

Gem5 samples at 1 ns but its six-significant-digit text labels can lose precision
for long latencies. Here the rare 3.75 ms label resolves to 10 ns. The benchmark
records `histogram_label_resolution_ns`, and the dashboard describes this export
limitation. This sample lies above both percentile thresholds, so the reported
p95/p99 below do not depend on its rounded coordinate.

| Guest DRAM read cost, including admission waiting | Baseline | 75% aggressor |
| --- | ---: | ---: |
| Mean | 77.562504 ns | 80.447173 ns |
| P95 | 132 ns | 142 ns |
| P99 | 331 ns | 387 ns |
| Samples | 1,744,668 | 1,850,752 |
| Guest admission retries | 0 | 153 |
| Physically served read/write bandwidth | 0.136502 GB/s | 34.275748 GB/s |
| Delivered effort cadence | 183.216 Hz | 176.037 Hz |

The 75% point offered 37.840176 GB/s and accepted 34.131342 GB/s of aggressor
traffic. It recorded 71,257 aggressor blocked attempts/stall cycles; pending work
waited under congestion. Total physical traffic was 16.736% of the theoretical
204.8 GB/s peak. Offered pressure is relative to the measured 50.453568 GB/s
random-traffic capacity, not the theoretical bus peak.

| Guest computation, mean/P95/P99 | Baseline | 75% aggressor |
| --- | ---: | ---: |
| Perception + estimation | 25.688 / 28.357 / 29.668 ms | 26.824 / 29.665 / 30.874 ms |
| Planning | 82.135 / 119.891 / 119.891 ms | 82.968 / 113.145 / 113.145 ms |
| Arm control | 0.214 / 0.258 / 0.313 ms | 0.220 / 0.288 / 0.314 ms |

These are guest simulation-time costs from received task-window records:
44 perception and seven planning callbacks in each case, with 274/267 control
callbacks whose start and completion fall within the respective task windows.
Timing correspondence separately matches every delivered task command stamp,
275/275 and 264/264, including timings that started before launch. That check does
not prove receipt of every record generated by the guest. All stream sequences
are contiguous. No valid sampled image diagnostic exceeds the 66.667 ms budget;
the maximum sampled ages were 46.138 and 53.340 ms.

Before changing the parser, the original execution fingerprint, simulator,
kernel, guest bundle, memory configuration and base-image metadata were checked
against the saved `4cb3a2a` provenance. Raw CPU/configuration, launch-boundary
reset, histogram counts, telemetry and independent final cup containment also
passed. Both memory windows are 1.5005 s, retaining the 0.5 ms cleanup tail.
Gem5 finished cleanly. The already observed bridge step-mismatch warning occurred
after scoring during shutdown; ROS/Gazebo children required SIGTERM.

The baseline recorded 10,023.387 wall seconds (2 h 47 min). Recovery cannot
reconstruct the 75% runner's exact final cleanup/export elapsed time, so its
14,849.100 seconds through the last exchange are shown as a lower bound
(`≥ 247.5 min`) in the dashboard, with an explanatory tooltip and exported scope.

The original manifest is archived as
`experiment_manifest.before-scientific-latency-recovery.json`. Recovered metrics,
raw hashes, validation checks, original failure and analysis parser hash are
saved in the suite/attempt evaluations; original execution provenance remains
intact. The original dashboard now includes both catches and their timings.
The unrun 125% point is skipped because the user requested 90% instead.

The new `results/memory-orin-demo-warmup0-90` continuation preserves all fixed
settings and imports both completed trials by local links, recording their source
provenance. It has a fresh configuration identity and current execution
fingerprint; resume checks are not weakened to accept a changed runner. It also
links the verified compatible calibration. Only the new `case-002` at 90%
(45.4082112 GB/s offered demand) is pending. Its launcher and monitoring commands
are above. No full guest simulation has been started by the agent.

The parser/report/telemetry checks pass 76 tests, including scientific labels,
duplicate rounded labels, final-snapshot selection, invalid bins and true sample
count mismatches. The actual-data dashboard is checked separately before handoff.
These two catches do not establish reliability or locate the failure boundary.

### Docker integration and fractional pacing, 2026-10-14

The user's remaining local 90% case completed with a verified launch and **CATCH**,
0.532 s of final containment and a 1.5005 s memory window. It retained 1,820,938
guest read samples: admission-inclusive mean 78.522950 ns, p95 143 ns and p99
387 ns. Timing correspondence is partial by one final command (218/219), and
the dashboard keeps that limitation visible. These records remain under
`results/memory-orin-demo-warmup0-90` with their original execution provenance.

Checking achieved bandwidth exposed a source pacing bug: both the nominal 75%
and 90% random-traffic cases accepted about 34.131 GB/s despite different offered
rates. After accepting a request, the old deadline update clamped fractional
deadlines to the next integer controller cycle. This repeatedly discarded
fractional credit and made distinct pressures issue the same traffic. The fix
preserves fractional deadlines while clamping overdue work at the current cycle;
issue remains bounded to one request per cycle, and admission backpressure still
retains pending work. Historical results describe their recorded achieved traffic
and are not reinterpreted as accurate nominal-load comparisons.

The rebuilt simulator passes memory-only stream pacing checks at the new levels:

| Pressure | Offered GB/s | Accepted GB/s |
| --- | ---: | ---: |
| 50% | 25.600384 | 25.600384 |
| 80% | 40.9606144 | 40.960640 |
| 90% | 46.0806912 | 46.080640 |

All relative errors are below 0.000112%, against the 0.5% regression tolerance.
The gate would reject the old quantized rates and is included in Docker
preparation. Seven Ramulator integration cases passed: single/vector correctness,
all three patterns, backpressure/retries and aggressor draining. No full-system
guest benchmark was used for these checks. The calibrated stream/stride/random
capacities are 51.200768/20.060352/50.453568 GB/s. They measure the bounded source
and controller together; the source's one-request-per-cycle ceiling can limit
throughput before the theoretical 204.8 GB/s bus peak.

The Docker workflow builds an application image over the shared SDK, resolves
installed package prefixes for guest staging and launcher setup, freezes host
scripts and memory configuration, calibrates each pattern once and stages one
guest bundle. Every case has its own private network/IPC, `/tmp` sockets and
output directory. The generic scheduler controls concurrency and pins the image
ID. Read-only disk/kernel inputs and per-simulation COW overlays preserve the
existing Ubuntu 22.04 / ROS Humble guest. Workers keep strict configuration and
execution-provenance resume checks. A supplied source revision permits frozen
exports to run without a mounted Git checkout.

Real Docker probes confirmed that two concurrent workers can bind the same three
Chimaera socket names independently, that inputs are read-only, and that KVM and
all four ROS packages are available. The full-system configuration instantiated
at **tick zero** with four Timing cores and no simulation. A short native Docker
throw also produced **CATCH**, with a verified launch and 0.5315 s containment.
This smoke test checks Gazebo/application dependencies; it is not a coupled
gem5 result. Records are under `results/docker-integration-20261014`.

The host workflow tests passed 148 checks, including the ten-case matrix, single
baseline, frozen-input validation, portable package prefixes, worker reuse/resume,
isolated Docker commands, aggregate failure handling and fractional-rate gating.
Preparation/reporting and actual worker handoff are checked separately in Docker.
The final prepared suite remains entirely pending; no full benchmark is started
by the agent. Run/monitor/report and image-transfer commands are in the container
README. Full coupled validation remains user-run on the larger system.
