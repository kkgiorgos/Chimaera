# Chimaera Fast DDS middleware implementation

Work branch: `feat/chimaera-dds-middleware`, based on `ed9c7a6`.

The middleware implementation passes the requested end-to-end catch and complete
message delivery checks. **It does not yet improve image latency:** complete
stereo-pair mean delay increases 8.5%, from 19.08 to 20.70 ms. Effort-command mean
delay improves 55.8%, and small-message means improve 16.0%/38.1%. Whole-guest
instructions and wall time increase in both workloads. Keep this mode opt-in;
the measurements do not support replacing the legacy bridge for image performance.

## Objective and acceptance

Route configured ROS topics between their original publishers and subscribers
through a Fast DDS transport backed by Chimaera's existing timed controllers.
Application publish/subscribe APIs stay unchanged. The bridge processes retain
simulation scheduling and device ownership, but do not subscribe to or republish
application data in this mode.

Acceptance requires a complete ball-catching trial, initially using KVM, and a
matched message-latency comparison. The initial robot case is the existing lofted
3 m/s throw, 480x360 stereo at 30 Hz, 250 Hz control, no memory aggressor. Retain
catch scoring, perception/planning/control records and exact artifact identities.
KVM establishes execution correctness; detailed CPU runs establish simulated
latency. Compare source/receipt counts, ordering, frame age, callback latency and
wall time against the current bridge, including native references.

## Design

The transport carries RTPS packets, including discovery and reliability traffic,
through bounded per-side runtimes. A runtime owns the DMA device once and serves
the ROS application processes over local IPC. The existing timing controller
continues to admit host input and expose guest output at completed boundaries.
Transport callbacks never advance either simulator or invoke application callbacks.

A pinned `rmw_fastrtps` 6.2.10 overlay installs the descriptor in participating
processes. Endpoint hooks translate selected host/guest topic names to a common
wire name and advertise Chimaera locators for the configured endpoints. Ordinary
local Fast DDS transport remains available. The first implementation preserves
normal typed deserialization; adopting image buffers in application callbacks is
a later, independently measured optimization.

Integration must account for route direction/type validation, local fanout,
ROS graph visibility, executor wakeups, discovery during startup, reliable
backpressure across paused peers, stereo pairing, and shutdown with held buffers.
Keep unsupported configurations explicit rather than silently changing delivery
semantics. Do not describe the implementation as zero-copy.

## Baseline acceptance

The clean KVM run passes the requested lofted baseline with the normal application
publish/subscribe APIs and sensor QoS. No clock probe, DMA diagnostics or memory
aggressor was enabled. The complete `ball_catching_robot` package is unchanged
from `ed9c7a6`.

| Result | Observed value |
| --- | ---: |
| Final score | `success: true`, `inside_cup_at_deadline` |
| Flight termination | `cup_capture` |
| Launch / evaluation | 0.5245 / 2.0245 simulation seconds |
| Containment | 0.531 s |
| Completed perception updates during the throw | 45 |
| Delivered effort commands during the throw | 234 |
| Delivered control rate / configured rate | 155.7 / 250 Hz |
| Telemetry sequence gaps | 0 |
| Whole-trial wall time | 423.1 s |

The requested rates are configuration values; this functional run does **not**
demonstrate sustained 250 Hz control. Command gaps have an observed p95 of
13.5 ms. Received perception records have mean input age 34.09 ms and p95
43.27 ms. Sampled control feedback age averages 32.10 ms, p95 41.47 ms; those
samples include only control updates accepted by the application's freshness
gate. The existing controller rejects feedback at least 50 ms old.
The native functional preflight also catches the ball and delivers 224.5 Hz
control. That host-native run is a functional reference, with different
execution hardware and scheduling; it does not isolate middleware overhead.

The [retained result and provenance](measurements/chimaera-dds-middleware-2026-10-21/)
include launch verification, telemetry coverage, runtime file hashes and exact
disk/kernel/module/simulator identities. An earlier diagnostic run also caught
the ball. A subsequent clean attempt failed during discovery with the old
controller queue limit and remains recorded. The final clean run uses the
middleware-specific bounded queue described below.

KVM wall time is dominated by coupling: 56.6% of stepped wall time is spent in
Gazebo advancement/statistics acknowledgement and 41.0% in the remaining
boundary work, primarily the existing settles. gem5 advancement accounts for
2.4%. Faster guest computation alone therefore has limited effect on this
run's wall time. These fractions describe this run, not an isolated simulator
speed comparison.

## Implementation and correctness

- Created the feature branch from the current transport implementation.
- Confirmed the disposable Docker SDK contains ROS Humble, Fast DDS 2.6.12,
  rmw_fastrtps 6.2.10 and rclcpp 16.0.21, with KVM passthrough available.
- Implemented the custom Fast DDS transport and pinned RMW hooks, bounded
  per-side routers, middleware selection, relocatable guest staging and ROS
  graph alias queries. An explicit ABI marker rejects unpatched RMW plugins.
- Native transport tests pass: three complete 640x480 images cross the
  coordinator-controlled path with every pixel checked; reverse acknowledgements,
  graph discovery and alias queries work with normal `rclcpp` APIs. Router tests
  cover boundary admission, malformed packets, queue overflow, endpoint reference
  counts and connection cleanup.
- 230 bridge/ball/middleware workflow tests pass, with two environment-dependent skips;
  both robot CTest targets pass. The native baseline catches the ball, with
  `inside_cup_at_deadline` and `cup_capture` scoring.
- Rebuilt the simulator with `ed9c7a6`'s interrupt acknowledgement fix after
  identifying lost ATA interrupts in the older SDK binary. The corrected binary
  boots the four-core guest and reaches the application workbegin barrier.
- Removed the UDP packet-size ceiling for explicit local-only participant
  profiles. A full 640x480 RGB image now crosses the tested path in one RTPS
  packet. The matched benchmark gives both implementations the same 1 MiB
  SHM-only profile to keep packet-size tuning constant in the comparison.
- A fresh-boot RTPS burst can exceed the legacy 1,024-message controller limit.
  Middleware-mode DMA queues now allow 8,192 packets with the same 32 MiB byte
  bound. Native routers remain bounded at 64 MiB. Both DMA queue constructors
  have regression coverage for the larger count and its overflow rejection;
  legacy bridge sessions keep their original limit.
- All nine transport and 14 bridge CTest targets pass after this change. The
  additional guest-library staging target also passes; it checks dependency
  resolution, path handling and rejection of incomplete middleware bundles.

## Matched latency measurement

All eight matrix cases are complete, with no missing, duplicate or reordered
messages. Each workload has native-free
and native-stepped controls, legacy bridge/DMA and Fast DDS/DMA cases. The same
endpoint binaries, RMW overlay and SHM-only 1 MiB DDS profile are used throughout.
The selected source window is 1 s, with 0.25 s warmup and 0.5 s drain; one repeat
and 30 scheduled frames per camera provide limited evidence for latency tails.

### Stereo and robot topic traffic

Both implementations deliver all 30 images per camera and all 30 complete
stereo pairs, with every image byte verified. Both also deliver 250 joint
messages, 250 effort commands and 1,000 clock messages.

| Stream | Samples per implementation | Bridge/DMA mean | Fast DDS/DMA mean | Mean delay change |
| --- | ---: | ---: | ---: | ---: |
| Left image | 30 | 9.683 ms | 11.391 ms | +17.6% |
| Right image | 30 | 19.082 ms | 20.704 ms | +8.5% |
| Complete stereo pair | 30 | 19.082 ms | 20.704 ms | +8.5% |
| Joint feedback | 250 | 11.479 ms | 13.357 ms | +16.4% |
| Effort command | 250 | 4.357 ms | 1.926 ms | −55.8% |
| Clock | 1,000 | 12.247 ms | 14.756 ms | +20.5% |

Stereo-pair p95 rises from 20.698 to 23.461 ms, and its observed maximum rises
from 21.142 to 23.676 ms. Mean pair age rises from 19.748 to 21.370 ms. Effort
command p95 improves from 13.856 to 5.227 ms; mean age, which also includes
source scheduling lag, improves from 7.112 to 5.136 ms. Clock maximum delay
increases from 28.119 to 49.354 ms. These are mixed results, not a general
latency improvement.

The left-camera delay increases by 1.71 ms, while mean camera callback skew
stays near 9.4 ms (9.399 → 9.313 ms). Most of the added pair delay is therefore
already present at the first image callback. The shared image-verification
workload continues to consume guest CPU between callbacks. This observation
does not isolate the added cost among IPC, packet handling, copies, DDS work
or scheduling; that needs stage-level profiling.

Whole-guest retired instructions increase from 1,018.2 to 1,103.6 million
(8.4%). Case wall time increases from 3,893.7 to 4,682.3 s (20.3%). The statistics
span 1.766 and 1.770 simulation seconds respectively, including export after
the matched source/drain window. These costs reinforce that this implementation
has not solved the image-path bottleneck.

### Small messages

Both implementations delivered all 97
measured messages in each direction, ordered and unique. The same 64-byte
application body is published at 97 Hz independently in both directions.

| Direction | Bridge/DMA mean | Fast DDS/DMA mean | Observed mean reduction |
| --- | ---: | ---: | ---: |
| Host → guest | 0.553 ms | 0.465 ms | 16.0% |
| Guest → host | 1.582 ms | 0.980 ms | 38.1% |

The host→guest observed maximum increases from 0.977 to 7.230 ms, despite
lower mean and p95. One sample determines p99 in a 97-sample window; this does
not establish a stable tail improvement. Whole-guest retired instructions over
the identical 1.752 s ROI rise from 271.3 million to 345.1 million (27.2%). Case
wall time rises from 1,119.1 to 1,459.9 seconds. These counters include OS,
discovery, reliability, warmup, drain and telemetry work; they do not isolate
bridge code. The measured callback-delay improvement has a CPU/wall-time cost
in this small-message workload.

Full per-stream means, percentiles, ages, release lags, source rates, native
controls and delivery counts are retained in the
[comparison JSON](measurements/chimaera-dds-middleware-2026-10-21/middleware-comparison.json),
[CSV](measurements/chimaera-dds-middleware-2026-10-21/middleware-comparison.csv)
and [interactive report](measurements/chimaera-dds-middleware-2026-10-21/report.html).
The eight-case [attempt record](measurements/chimaera-dds-middleware-2026-10-21/attempts.json)
and compressed gem5 statistics retain provenance and resource counters.

### Method and reproduction

The latency guest has two 3 GHz TimingSimpleCPU cores, 16 KiB L1 instruction/data
caches and a 256 KiB L2, with 8-/16-way associativity and DDR3_1600 memory.
The coupling interval is 2 ms, the transport poll period 1 ms and the physics
step 1 ms. All cases use reliable depth-128 delivery, deterministic image-byte
verification, a 16 MiB SHM segment and a 1 MiB RTPS packet limit. There is no
memory aggressor. Both directions generate independently; this is not an echo
round-trip measurement.

The typed ball-traffic workload carries two 480x360 RGB images at 30 Hz each,
joint feedback and effort commands at 250 Hz each, and clock traffic at 1 kHz.
It exercises message transport without the robot's vision and control
computation. The functional catch uses four KVM cores and the existing
application's 1 kHz joint feedback, 0.5 ms coupling/physics steps and 0.1 ms
transport polling; its CPU/memory geometry and application QoS
differ from the isolated message benchmark. Results from those runs must not
be interpreted as a matched CPU-performance comparison.

Delay starts at actual publication and ends on entry to the receiving callback;
image-byte checking follows that timestamp. That checking still consumes guest
CPU and can delay subsequent callbacks, so this is a verified-message workload,
not an isolated serialization microbenchmark. Complete-pair delay runs from the
first camera publication until both receiving callbacks have started.
Age starts at the scheduled release,
so it also includes source scheduling lag. Native controls use 1 ms timestamp
buckets and cannot establish sub-millisecond timing precision. One short repeat
does not establish repeatability, robust tail percentiles or a hard real-time
guarantee.

Follow [the overlay build and staging instructions](../middleware/README.md),
then run the matrix with the measured window:

```sh
python3 middleware/benchmark.py --output /path/to/new-output \
  --physical-host ubuntu-george --runtime /path/to/overlay \
  --bundle /path/to/latency-guest.tar.gz --gem5-root /path/to/gem5 \
  --image /path/to/guest.img --kernel /path/to/kernel \
  --dma-module /path/to/chimaera_dma.ko --cpu-type timing \
  --duration 1 --warmup .25 --drain .5 --timeout 7200
python3 middleware/report.py /path/to/new-output/evidence/attempts.json \
  --output /path/to/new-output/evidence
```

Only one native Chimaera session may run at a time. The retained evidence records
the physical host, source revision/patch, active CPU classes and exact simulator,
disk, kernel, module, bundle and runtime-library hashes. The comparison checks
hardware, runtime and DDS fingerprints and withholds improvement percentages
when delivery is incomplete or source counts differ.

## Scope and remaining costs

Configured topic data bypasses the generic ROS subscriber/publisher handoff in
the coordinator. Typed application serialization/deserialization, local IPC
copies, DMA transfer and boundary admission remain. This is not zero-copy.
The coordinator continues to own simulator progress and device access; the
middleware does not bypass causal admission.

This qualification covers fresh boots, local participant profiles and the
baseline application's existing topic QoS. Services/actions, checkpoint restore,
networked deployments and finite deadline/lifespan/liveliness policies require
separate validation. The RMW patch is pinned to Humble's 6.2.10 sources and
Fast DDS 2.6.12. The clean catch establishes this scenario's execution correctness;
it does not demonstrate sustained 250 Hz control or broader robot acceptance.
