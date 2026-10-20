# Chimaera message latency benchmark

The [20 October 2026 stack profile](chimaera-latency-profile-2026-10-20.md)
attributes delivery delay to guest timers, callback queues, polling, synchronization
boundaries and bulk memory transfers, with per-message traces and CPU samples from
the `ubuntu-george` deployment.

The benchmark measures **application publish to receiving callback in simulation
time**, separately for host-to-guest and guest-to-host traffic. Independent
periodic sources run concurrently with adjustable body size, rate and QoS:

- `native_free`: both endpoints run on the host; Gazebo runs normally at target
  real-time factor one after discovery.
- `native_stepped`: both endpoints run on the host; Gazebo uses the same physics
  step, synchronization interval, verified `multi_step` completion, and 20 ms
  callback settling windows as Chimaera. This control has no gem5 execution phase.
- `chimaera`: the endpoints exchange data through the production universal bridge,
  controller queues, sockets and m5 operations. Select KVM, TimingSimpleCPU or O3,
  with configurable cores, clock, L1 data/instruction sizes, L2 size and cache
  associativity. The starting configuration is two KVM cores at 3 GHz.

Differences include DDS hops, guest OS scheduling, polling and synchronization;
they do not isolate socket or m5 cost. KVM provides initial measurements and
harness validation, not detailed processor timing. The workload includes no
robot, rendering, application computation or memory aggressor.

## Local interactive workbench

Start the separate local app from the repository root. Its HTTP server uses the
Python standard library and its page has no Node build or CDN dependencies:

```bash
PYTHONPATH=ros/chimaera_ros_ws/src/chimaera_ros_bridge \
  python3 -m chimaera_ros.latency_dashboard --repo-root "$PWD"
# Open http://127.0.0.1:8787
```

An installed runtime also exposes `ros2 run chimaera_ros_bridge chimaera_latency
serve --repo-root "$PWD"`. Optional server arguments are `--port`, `--data-root`
and `--runtime-prefix`. The server binds only to loopback.

Open **Runtime** to inspect prerequisites and asset paths. **Build and stage**
builds the ROS benchmark and bridges with colcon, using two compiler jobs, then
creates a boot-time guest bundle. It does not compile gem5, install system
dependencies or download a disk/kernel. **Stage current runtime** packages the
selected existing prefix. Rebuild/restage after changing the collectors. Version
3 bundles support per-case CPU selection and the typed traffic event protocol;
older bundles show as unready and require Build and stage.

Use the visual controls to select traffic, timing pairs and named gem5 hardware
profiles. **Add configuration** adds another profile; each has a CPU model,
core count, frequency and expandable cache controls. All profiles share matching
native baselines. The JSON editor and configuration import/export use the same
validated schema as the preview and runner. For example, replace `gem5_configs`
in an exported configuration with:

```json
"gem5_configs": [
  {"name": "KVM", "cpu_type": "kvm", "num_cores": 2, "cpu_clock": "3GHz"},
  {"name": "Timing 4-core", "cpu_type": "timing", "num_cores": 4,
   "cpu_clock": "2GHz", "l1d_size": "32KiB", "l1i_size": "32KiB",
   "l2_size": "1MiB", "l1_assoc": 8, "l2_assoc": 16},
  {"name": "O3", "cpu_type": "o3", "num_cores": 2, "cpu_clock": "3GHz"}
]
```

Unspecified profile settings use 16 KiB L1 caches, a 256 KiB L2, and 8/16 ways.
Detailed models boot with KVM and switch at the first workbegin, before the
measurement clock starts; they still require `/dev/kvm`. There is no separate
cache-warming phase before that switch. Warmup occurs on the selected model.
Cache parameters are retained for KVM profiles, but KVM does not simulate their
timing. `gem5/config.ini` and `gem5/latency_clock_origin.json` record the configured
hardware and active measurement CPUs.

The app runs one job at a time. The monitor shows completed cases, actual
simulation-clock progress and bounded log tails; boot time has no invented ETA.
**Stop** terminates owned process groups and keeps completed cases. State, raw
events, logs, bundles and exports live in `.chimaera-latency/` by default. Reloads
retain history; stopping the server cancels its active job. After an unexpected
restart, unfinished jobs are marked interrupted rather than silently resumed.

Failed, stopped and interrupted sweeps offer **Resume unfinished cases** in the
monitor. Set a wall timeout for the remaining cases, then resume. Completed cases
are reused only when the endpoint and physical clock plugin fingerprints still
match. Failed attempt directories and their logs remain intact; a retry writes
to a new `case_NNNN_attempt_NNN` directory. Resume records the timeout change
without changing traffic, CPU profiles, synchronization or measurement windows.

Fine synchronization steps can require much more wall time than the simulated
window. For example, 10 s measurement + 0.1 s warmup + 0.2 s drain at 2 ms requires
5,150 steps. The controller's settling delays alone take at least 206 wall
seconds. With the observed Fortress world-statistics acknowledgement cadence,
this native case needs approximately 525 wall seconds including startup; a
300 s timeout stopped an otherwise advancing run at 5.922 simulated seconds.
The plan warns about short timeouts and resume suggests a larger budget. These
estimates are advisory: detailed gem5 execution and host load can add more time.
Timeout errors show both elapsed simulation progress and the requested window.

Results emphasize added latency against both native controls, with filters for
hardware profile, body size, rate and repetition. Choose a heatmap cell for its
raw distribution, time trace, delivery counts and matched controls. When all
repetitions are selected, cells average the chosen **run statistic**; they do not
pool packets into a new percentile. Plot views retain at most 2,000 points while
statistics use all receipts. Raw event CSV and numerical report exports remain
available. **Import results** reads earlier CLI sweep directories without
modifying them. All four Catppuccin flavors and fourteen accent colors persist
in the browser.

## Ball-catching traffic experiment

Select **Ball-catching traffic** from the preset menu. The reusable configuration
is [ball-catching.json](../ros/chimaera_ros_ws/src/chimaera_ros_bridge/latency_web/experiments/ball-catching.json).
It runs all of these streams concurrently in each native or Chimaera case:

| Stream | Direction | Actual ROS message | Default rate / shape |
| --- | --- | --- | --- |
| Left camera | Host → guest | `sensor_msgs/msg/Image` | 90 Hz, 640×480 RGB8 |
| Right camera | Host → guest | `sensor_msgs/msg/Image` | 90 Hz, 640×480 RGB8 |
| Joint feedback | Host → guest | `sensor_msgs/msg/JointState` | 250 Hz, seven names, positions and velocities |
| Effort command | Guest → host | `sensor_msgs/msg/JointState` | 250 Hz, seven names and efforts |
| ROS clock | Host → guest | `rosgraph_msgs/msg/Clock` | One sample per physics step; 1,000 Hz at 1 ms |

The images alone request 158.2 MiB/s before ROS/transport framing. Readiness
`std_msgs/msg/Bool` messages add nominal 25 Hz arm-ready and 10 Hz perception-ready
background traffic. They carry no sample identity and have no individual latency
statistics. Optional diagnostic JSON telemetry and guest-internal odometry,
geometry and trajectories are excluded from this bridge-traffic workload.

The shapes/rates come from `ball_catching_sim/launch/application.launch.py`,
`ball_catching_sim/src/host.cpp`, `ball_catching_robot/src/effort_control.cpp` and
`ball_catching_robot/src/perception.cpp`. Route policies follow
`scripts/run_memory_suite.py`: best-effort image depth 2, paired stereo replacement,
best-effort depth 1 for joint feedback/effort/clock, latest clock replacement, and
reliable depth 1 readiness flags. Images expire after two camera periods by
default (22.222223 ms at 90 Hz). Application sensor consumers use depth 5,
effort reception uses depth 1, and synthetic camera/clock producers use a depth
10 reliable publisher. The benchmark retains its 128-entry bridge queue limit;
the full ball-catching suite uses a 16-entry limit.

The preset includes one KVM core at 3 GHz and both native controls, 50/20/5 ms synchronization,
10/1 ms polling, and skips polling ≥ synchronization: nine cases. Its windows
are 0.1 s warmup, 1.5 s measurement and 0.5 s drain, with one repetition. Change
the resolution/rates, image-age budget, timing axes and gem5 profiles in the form
or JSON editor. Global synthetic body-size/rate and QoS controls are hidden for
this workload because each typed stream has its own shape and queue policy.
The starting CPU profile uses the one-core configuration validated below;
core count and CPU/cache settings remain editable.

Results select one message stream at a time. Each heatmap, baseline difference,
CDF and time trace uses that stream's receipts; camera, joint and clock packets
are not pooled into a latency percentile. Exports include stream identity,
stream rate and actual serialized byte counts. Empty or sparse image cells can
reflect paired replacement, age rejection, DDS loss, or arrivals after draining;
they are not converted to zero latency. Coarse synchronization can remove most
camera frames, so inspect delivery counts alongside added latency.

No benchmark fields are inserted into the ROS messages. Normal header timestamps
(or `Clock.clock`) carry the nominal capture/release time, identifying samples
within a stream and pairing left/right frames. Sources separately buffer the
actual publish-call timestamp. Typed receive records retain an unsigned sentinel
in `publish_ns`; analysis joins source records by direction, stream and sequence
after export. The latency still uses **actual publication → callback entry**,
and the measurement window selects actual publication times. Original CSV stays
unchanged. Synthetic byte messages retain their existing 40-byte envelope.

Pixels and joint values are placeholders. Commands are an independent periodic
source. The workload runs no rendering, perception, planning, control algorithm
or memory aggressor, and makes no catch-success claim. Source scheduling uses
the benchmark's authoritative clock; delayed ROS clock messages do not alter
that schedule. It represents steady nominal traffic rather than the full
application's computation, feedback dependencies or startup transitions.

To run the same saved experiment from the CLI after building and staging:

```bash
ros2 run chimaera_ros_bridge chimaera_latency experiment \
  ros/chimaera_ros_ws/src/chimaera_ros_bridge/latency_web/experiments/ball-catching.json \
  --output /tmp/ball-catching-traffic \
  --guest-bundle /path/to/current-v3-guest.tar.gz
```

Export an edited configuration from the app to use different windows or CPU
profiles in that command. Every case retains its frozen parameters and commands.

Typed-traffic validation on 2026-10-19 passed all ten CTest targets, including
32 Python analysis/dashboard checks and an actual ROS wire-shape/export check.
Browser validation covered preset loading, per-stream plots, both directions,
15-row stream CSV exports and 390 px layout. A three-case run at 5 ms sync /
1 ms polling used 0.1 s warmup, 0.3 s measurement and 0.3 s drain with one KVM
core at 3 GHz. Both native controls and KVM completed with receipts in all five
measured streams. KVM received 17/27 frames from each camera, 59/75 joint samples,
42/76 effort commands and 42/300 clock samples. Two-core validation also
encountered a pre-workbegin guest disk I/O startup stall; stopping it preserved
the completed native cases. These are functional checks with limited samples,
not stable estimates of performance or loss probability.

## Build

From the repository root, with Ubuntu 22.04, ROS Humble and Gazebo Fortress:

```bash
source /opt/ros/humble/setup.bash
export CMAKE_BUILD_PARALLEL_LEVEL=2
colcon --log-base ros/chimaera_ros_ws/log build \
  --base-paths ros/chimaera_ros_ws/src \
  --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install \
  --packages-select chimaera_ros_bridge --executor sequential \
  --cmake-args -DBUILD_LATENCY_BENCHMARK=ON -DBUILD_GAZEBO_BRIDGE=ON \
    -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
source ros/chimaera_ros_ws/install/setup.bash
```

The benchmark option is off by default and additionally requires Fortress's
`libignition-gazebo6-dev`. Guest builds require the custom libm5 and normal bridge
dependencies. Build in an environment matching the guest image.

## Native checks

Each output directory must be new. It retains exact commands, parameters, world,
configuration, logs, raw events and summaries.

```bash
ros2 run chimaera_ros_bridge chimaera_latency run \
  --mode native_free --output /tmp/latency-native-free \
  --payload-bytes 64 --rate-hz 20 --duration 2
ros2 run chimaera_ros_bridge chimaera_latency run \
  --mode native_stepped --output /tmp/latency-native-stepped \
  --payload-bytes 64 --rate-hz 20 --duration 2 \
  --interval-us 50000 --poll-us 10000
```

Defaults: 0.5 simulated seconds of warmup, 2 seconds of source measurement,
0.5 seconds of draining, 1 ms physics steps, reliable volatile keep-last QoS with
depth 128, and a 300 second wall timeout. Adjust these with `--warmup`,
`--duration`, `--drain`, `--physics-step-ns`, `--reliability`, `--depth` and
`--timeout`. Short runs check the harness; they do not establish a stable p99.

## Stage and run the guest

Stage once after rebuilding. A bundle installs the runtime inside the guest at
boot, in gem5's temporary copy-on-write disk layer. This needs no host image
mount or sudo. Per-case settings and routes use the existing readfile mechanism.

```bash
ros2 run chimaera_ros_bridge chimaera_latency stage \
  --output /tmp/latency-guest-root --bundle /tmp/latency-guest.tar.gz
ros2 run chimaera_ros_bridge chimaera_latency run \
  --mode chimaera --output /tmp/latency-chimaera \
  --guest-bundle /tmp/latency-guest.tar.gz \
  --payload-bytes 64 --rate-hz 20 --duration 2 \
  --interval-us 50000 --poll-us 10000
```

The default gem5 root is `./gem5`; its ROS Humble disk and kernel under
`resources/` are used. Override with `--gem5-root`, `--image` and `--kernel`.
The image needs normal readfile startup and privileged bridge mapping. The host
must expose readable/writable `/dev/kvm`. Run one Chimaera session at a time
because its data and timing sockets have fixed paths.

An existing offline deployment at `/opt/chimaera/latency` can also be used by
omitting `--guest-bundle`. To deploy the staged tree to an image, stop gem5/QEMU
using it and run the universal `deploy_image.sh IMAGE STAGED_ROOT PARTITION`.

## Sweep and compare

```bash
ros2 run chimaera_ros_bridge chimaera_latency sweep \
  --output /tmp/latency-sweep \
  --guest-bundle /tmp/latency-guest.tar.gz \
  --modes native_free native_stepped chimaera \
  --payload-bytes 64 1024 --rate-hz 20 100 \
  --interval-us 50000 20000 10000 --poll-us 10000 5000 1000 \
  --cpu-type kvm timing o3 \
  --duration 10 --warmup 1 --drain 1 --repeats 3
ros2 run chimaera_ros_bridge chimaera_latency report /tmp/latency-sweep
```

The sweep starts at 50 ms synchronization / 10 ms polling, skipping pairs with
polling greater than or equal to synchronization. Normal native runs once per
size/rate/repetition; stepped native runs once per interval. Polling has no effect
on native operation. CPU profiles also share those native controls. CLI `run`
accepts one `--cpu-type`; `sweep` accepts several. Both accept `--num-cores`,
`--cpu-clock`, `--l1d-size`, `--l1i-size`, `--l2-size`, `--l1-assoc` and
`--l2-assoc`. To sweep different clocks/caches together, use named profiles in
the workbench. Every case starts fresh processes and, for Chimaera, boots
the same staged image. Infrastructure failure stops the sweep and preserves the
failed case. Regenerate the report to see completed and excluded cases.

`latency-report.html` summarizes measurements; CSV and JSON retain numerical
results. JSON also contains differences against both native controls, matched by
body size, rate, QoS, physics step, windows, repetition, host, RMW setting and
collector/clock-plugin binary fingerprints. Stepped controls also
match synchronization interval. Differences compare independent distributions:
a p99 difference is not the p99 of per-message added delay. Inspect variation
across repetitions before interpreting small differences.

## Clock contract

Native endpoints read one process-shared clock written directly by a Gazebo
`PreUpdate` plugin. Clock updates cross neither DDS nor Ignition Transport.
**A native zero means no physics clock tick elapsed**, not zero wall-time cost.
Default native granularity is 1 ms.

The Chimaera guest reads `m5_rpns_addr()` under KVM, or instruction-based
`m5_rpns()` on detailed CPUs, relative to first workbegin. The origin
uses the established bridge readfile initialization. This clock advances during
guest execution without `/clock`. The host logical clock updates to actual
completed gem5 ticks **before** the bridge republishes guest messages. It stays
at that boundary while Gazebo catches up. Actual ticks handle KVM overshoot.
This avoids negative command latency while gem5 is ahead of Gazebo.

The host timestamp is therefore a co-simulation delivery boundary, not a claim
that physics has already reached that instant. Host events additionally record
actual physics time in `physics_ns`. This benchmark does not measure subsequent
physical application of a command. Guest records lack independent host physics
time and use unsigned sentinel `18446744073709551615` for that field.

## Sampling and accounting

Every packet has a 40 byte header with magic, sequence number, requested release
time, publication time and body size. ROS CDR, routes and transport framing add
further bytes; `--payload-bytes` names only the adjustable body. Data routes
preserve FIFO with coalescing disabled. Both streams run together, including
bidirectional contention.

Endpoints check their source schedule once per 1 ms of local OS time, apart from
processing work. Frozen or jumping clocks can release a burst. `scheduled_ns`
and `publish_ns` preserve this behavior; release lag is separate from delivery
latency. Read actual publication counts/rates alongside requested rates. Even
spacing above the clock or executor's effective rate is not guaranteed.

Periodic traffic can align with polling or interval boundaries. A 20 Hz source
and a 50 ms interval, for example, repeatedly sample a similar phase. Include
rates such as 97 Hz alongside application rates such as 100 Hz when exploring
the latency distribution. Results characterize the configured release pattern;
they are not a bound over every possible arrival phase.

The receive timestamp is captured at callback entry before packet parsing or
record allocation. Records stay in memory through measurement and data draining;
only afterward does the guest export bounded reliable telemetry chunks. A final
sequence/count marker establishes complete telemetry; missing telemetry fails
the run. Clock reads, stamping and bookkeeping still add instrumentation work.

Statistics select actual source publication times in
`[warmup, warmup + duration)`. Late receipts during draining count; warmup messages
received during measurement stay excluded. Summaries contain nearest-rank
p50/p95/p99, mean, maximum, received/sent counts, duplicates, reordering, source
release lag and actual rate. Missing messages at the drain boundary may be lost
or arrive later. Percentiles describe received messages only; always inspect
delivery counts. At least 10,000 receipts give roughly 100 observations in the
top one percent; small checks say little about tails.

Queue/batch overflow, invalid clocks, mismatched records, incomplete telemetry
and process failures are explicit failures. High rates and large bodies may
exceed the controller's existing 32 MiB batch or 1024-message limits; increasing
DDS depth alone does not remove these limits.

## Verification

```bash
PYTHONPATH=ros/chimaera_ros_ws/src/chimaera_ros_bridge python3 -m unittest discover \
  -s ros/chimaera_ros_ws/src/chimaera_ros_bridge/test -p 'test_latency*.py' -v
ctest --test-dir ros/chimaera_ros_ws/build/chimaera_ros_bridge --output-on-failure
```

Analysis checks cover source-window selection, late receipts, censored loss,
duplicates/reordering, invalid clocks, incomplete runs and matched native
controls. Native Gazebo checks validate the executable and plugin independently
of gem5. Full KVM validation needs the staged guest and an available KVM device;
native results are not evidence that the guest path ran.

Validation on 2026-10-19: the Release CMake build and all seven CTest targets
passed, including twelve latency analysis/staging checks. A seven-case sweep
covered normal native Gazebo, native stepping at 50/20 ms, and all four KVM
combinations of 50/20 ms synchronization and 10/5 ms polling. With 64 byte bodies,
100 Hz, 0.1 s warmup, 0.5 s measurement, 0.2 s drain and one repetition, every
case delivered all 50 measured messages in each direction. Guest installation
used the boot-time bundle in the temporary disk overlay. These short runs
validate collection and comparison; they do not establish stable latency tails.

Workbench validation on 2026-10-19: all eight CTest targets passed, along with
25 latency analysis/dashboard checks. Browser checks covered configuration
validation, shared controls across CPU profiles, native-only results, raw-data
drill-down, CSV downloads, keyboard focus, four flavors, all accent contrasts,
390 px layout, reloads and a real service restart. Both the fresh colcon build
and cached rebuild/staging actions succeeded from the page.

UI-launched cases completed for native running/stepped, KVM and TimingSimpleCPU.
An O3 guest stalled before workbegin with disk I/O timeouts; Stop completed in
about two seconds and preserved the four completed cases. A subsequent O3 case
completed with one core at 2 GHz, 32 KiB L1 caches and 1 MiB L2; gem5's origin
metadata and `config.ini` confirmed the selected CPU, clock and cache sizes.
These high-rate, short-window checks intentionally retain censored delivery
counts and do not establish stable performance or reliability estimates.
