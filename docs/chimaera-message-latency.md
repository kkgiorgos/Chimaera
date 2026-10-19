# Chimaera message latency benchmark

The benchmark measures **application publish to receiving callback in simulation
time**, separately for host-to-guest and guest-to-host traffic. Independent
periodic sources run concurrently with adjustable body size, rate and QoS:

- `native_free`: both endpoints run on the host; Gazebo runs normally at target
  real-time factor one after discovery.
- `native_stepped`: both endpoints run on the host; Gazebo uses the same physics
  step, synchronization interval, verified `multi_step` completion, and 20 ms
  callback settling windows as Chimaera. This control has no gem5 execution phase.
- `chimaera`: the endpoints exchange data through the production universal bridge,
  controller queues, sockets and m5 operations. The first experiment uses two KVM
  cores with the existing 3 GHz board configuration.

Differences include DDS hops, guest OS scheduling, polling and synchronization;
they do not isolate socket or m5 cost. KVM provides initial measurements and
harness validation, not detailed processor timing. The workload includes no
robot, rendering, application computation or memory aggressor.

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
  --duration 10 --warmup 1 --drain 1 --repeats 3
ros2 run chimaera_ros_bridge chimaera_latency report /tmp/latency-sweep
```

The sweep starts at 50 ms synchronization / 10 ms polling, skipping pairs with
polling greater than or equal to synchronization. Normal native runs once per
size/rate/repetition; stepped native runs once per interval. Polling has no effect
on native operation. Every case starts fresh processes and, for Chimaera, boots
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

The Chimaera guest reads `m5_rpns_addr()` relative to first workbegin. The origin
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
  -s ros/chimaera_ros_ws/src/chimaera_ros_bridge/test -p test_latency.py -v
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
