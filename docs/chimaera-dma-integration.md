# Integrated coherent DMA transport

Implementation: `cfc5dc1` on `feat/chimaera-low-latency-transport`.
Measurements: `ubuntu-george`, 2026-10-20. All runs use isolated containers;
the deployed latency service and guest disk/kernel remain unchanged.

## What is integrated

The ROS host bridge, Gazebo timing controller and guest bridge can select
`transport_backend: dma`. Host messages enter bounded shared mailboxes while
gem5 is paused. `CHIMAERA_INGRESS` admits them at that completed boundary.
The PCI device fetches descriptors and transfers bytes through Ruby's coherent
timing DMA port. Driver IRQ completions wake the ROS executor through a guard
condition. Guest output becomes host-visible after the completed simulation
step. No polling sleep or asynchronous host arrival changes virtual causality.

Receive messages retain registered pages through synchronous ROS publication;
the last buffer owner reposts the slot. Outgoing bridge frames are constructed
directly in registered TX storage with reserved wire-header space. Heap fallback
has a reserved staging slot, so queued direct loans cannot starve an older heap
frame. Consecutive small frames share a bounded packet, amortizing IRQ/syscall
cost; bulk images retain separate buffers. Global FIFO is preserved.

The driver ABI remains 1. Integrated controller protocol 2 (`CHDMA002` / `CDM2`)
validates entire batches before exposing records and rejects prototype peers.
The benchmark uses sixteen RX and four TX slots of 8 MiB: 160 MiB allocated
before workbegin. The default driver geometry is unchanged. Per-packet capacity
is the configured slot size minus framing; larger packets require larger
driver buffers, up to the 32 MiB device limit. The tested large stereo packet
fits the 8 MiB pool. These are explicit bounds, not unlimited-file streaming.

The `m5` backend remains the default and is tested with the same new endpoint
binaries. DMA currently requires fresh boots. Checkpoint/reused-guest cases
are rejected. The gem5 configuration uses MP-table PCI routing with `acpi=off`;
production ACPI routing remains future work. The model accounts for coherent
memory traffic and interrupts, without a separate PCI link-rate limit.

## Bugs found by integration

**Interrupt notification could be lost.** `intrPost()` can migrate to the KVM
event queue and reenter the device ACK before returning. Updating `irqAsserted`
after the call overwrote the nested ACK and suppressed later notifications.
Publishing line state before posting/clearing fixes it. The retained
[trace excerpt](measurements/chimaera-dma-integration-2026-10-20/dma-irq-race-excerpt.txt)
shows the ACK at the exact posting tick, followed by completions with an
incorrectly asserted line. Repeated full-system smoke cases completed after the
fix. The standalone detailed-CPU probe was rerun against the fixed binary.

**The benchmark's default DDS segment was smaller than one image.** Fast DDS
2.6 defaults to 512 KiB, while each 640×480 RGB image contains 921,600 pixel
bytes. Its documentation warns that too-small segments risk overwriting data
during a send. Humble's localhost mode also appends default UDP and SHM
transports, defeating a naive custom-profile addition. The benchmark now
supports an explicit 16 MiB SHM transport and loopback UDP whitelist, disables
builtin transports, and sets `ROS_LOCALHOST_ONLY=0`. Identical profile bytes,
endpoint binaries and middleware settings are required in native, m5 and DMA
comparisons. This adapts the existing ball-catching runner's configuration.
([Fast DDS documentation](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/transport/shared_memory/shared_memory.html),
[Humble participant implementation](https://github.com/ros2/rmw_fastrtps/blob/humble/rmw_fastrtps_shared_cpp/src/participant.cpp))

**Compatibility/startup failures remain visible.** An empty optional
`dma_module:=` ROS argument broke m5 startup and is now omitted. Capability
markers reject old bundles before boot when DMA, lossless delivery or image
integrity checking is required. Malformed DMA startup failure persists across
subsequent controller calls.

## Acceptance method

The m5 reference includes the earlier streamed protocol, receive-buffer and
borrowed-publication rewrite. This comparison measures the subsequent DMA
integration against that milestone with the same endpoint binaries and DDS
configuration.

Each performance case has a 2 s source window and a 0.25 s warmup, followed by
0.5 s control or 0.75 s stereo drain. Detailed configuration matches deployment:
two TimingSimpleCPU cores, 3 GHz, 16 KiB L1I/L1D, 256 KiB L2, 8/16-way L1/L2,
MESI_TWO_LEVEL, DDR3_1600 and 3 GiB RAM. DMA permits 64 outstanding coherent
cache-line requests. Synchronization remains 2 ms; the m5 poll limit is 1 ms.
KVM boots the guest and is used separately for smoke checks, not acceptance.

Control traffic is 64 bytes at 97 Hz in each direction: 194 measured samples.
Stereo is 640×480 RGB, 30 Hz: 60 frames per camera, with 250 Hz joints/control
and a 1 kHz clock stream. `latest` preserves sensor QoS and explicit replacement
and expiry. `lossless` uses reliable depth 128, disables replacement/expiry and
keeps complete stereo pairs. This bounded policy is checked by actual counts;
it does not promise losslessness under arbitrary overload.
The typed ROS benchmark uses deterministic pixels and matches the deployed
message shapes, rates and QoS; vision and controller computation are excluded.

Every detailed case has native-free and native-stepped controls on the same
physical machine and within its isolated container. The exporter checks traffic
windows, policies, DDS bytes, endpoint/clock/profile fingerprints, middleware,
container identity and active detailed CPU classes. Native logical timestamps
use 1 ms physics-clock buckets; zero means the same bucket. The native gap
includes modeled guest CPU/memory behavior, rather than isolating bridge CPU
cost; the deployed guest also has its own kernel and userspace environment.
The m5/DMA comparison shares that guest environment. Host wall times are
recorded under shared machine load and container
limits; they are not isolated simulator speed ratios.

Callback delay runs from actual source publication to the receiving callback.
Age runs from scheduled source release, so Linux timer and scheduling delay
cannot disappear from the measurement. Pair age ends at the later camera
callback. Delivery counts, duplicates, ordering and source release lag accompany
latency. Sixty pairs improve on six-frame pilots but give limited evidence for
rare latency tails.
An attempt marked `complete` finished its window and telemetry export; that
status alone does not mean every stream arrived or a performance target passed.
These deltas are guest-visible/co-simulation time. Work on the host while gem5
is paused leaves that clock unchanged. Native-stepped callbacks can complete
inside a paused physics-clock bucket, so its sub-millisecond means are averages
of quantized observations. Native-free values are retained alongside them.
Guest benchmark callbacks read gem5's authoritative elapsed counter; host
callbacks read the completed gem5 boundary. Linux `CLOCK_MONOTONIC` and the
latest delivered ROS `/clock` sample can differ from this reference. Their
timer/transport behavior remains visible in release lag and clock-message age.
For a source timestamp denoting scheduled capture, use age; publication-to-
callback delay starts after any late source release.

## Detailed-CPU control result

All 194 measured publications in each direction arrived, with no duplicates or
reordering. Values below are simulation milliseconds. Each backend has its own
matched native controls; native means reflect 1 ms timestamp buckets.

| Path | m5 mean / p99 | DMA mean / p99 | DMA stepped-native mean | DMA native-free mean |
| --- | ---: | ---: | ---: | ---: |
| Host → guest | 4.742 / 10.370 | 0.544 / 0.651 | 0.057 | 0.098 |
| Guest → host | 4.099 / 8.019 | 1.060 / 1.096 | 0.046 | 0.062 |

The observed mean callback-delay reductions are 88.5% and 74.1%, respectively.
The corresponding m5 stepped-native means are 0.072/0.052 ms. Source scheduling
is a separate cost: DMA release-to-callback age averages 1.534/3.093 ms, versus
m5's 5.731/6.144 ms and DMA's stepped native 0.567/0.557 ms. Guest source release
lag alone still averages 2.032 ms. DMA fixes message transfer overhead without
fixing the guest timer/scheduling path. Future clock work must validate the
Linux-versus-gem5 per-core oracle and actual wakeups.

The m5 and DMA control cases took 985.6 and 1057.2 wall seconds under shared
host load. The guest-visible improvement is not a wall-time simulator speedup.
ROI instruction statistics, including warmup, drain and telemetry, are
146.1 million for m5 and 158.6 million for DMA. These counters include the whole
guest and do not isolate bridge code; reduced total CPU work is not established.

## Detailed-CPU latest stereo result

Both backends received all 60/60 complete stereo pairs at 30 Hz. The image
result shows little improvement over the m5 rewrite: mean pair callback delay
changes from 23.351 to 22.994 ms (1.5%), and mean scheduled-release age changes
from 24.018 to 23.661 ms. Each observed maximum age is approximately 31 ms.
Sixty samples and one repeat do not establish stable tail differences.

| Metric | m5 | DMA | DMA matched stepped native |
| --- | ---: | ---: | ---: |
| Left callback delay, mean | 14.903 ms | 14.721 ms | 0.167 ms |
| Right callback delay, mean | 23.351 ms | 22.994 ms | 0.133 ms |
| Complete-pair age, mean | 24.018 ms | 23.661 ms | 0.600 ms |
| Complete-pair age, p99 / max | 30.996 ms | 30.989 ms | 1.333 ms |
| Complete pairs received | 60/60 | 60/60 | 60/60 |

The m5 matched native pair ages are 0.583 ms stepped and 1.133 ms free-running;
the DMA matched free-running native mean is 1.217 ms. All native images arrived.
The left-camera observed p99 rises from 18.245 ms with m5 to 22.179 ms with DMA.
That tail and the mixed-stream losses rule out a claim of uniform improvement.

| Mixed stream | m5 receipts | DMA receipts | m5 / DMA mean callback delay |
| --- | ---: | ---: | ---: |
| Joint feedback | 490/500 | 446/500 | 10.279 / 7.993 ms |
| Effort command | 240/501 | 265/499 | 1.258 / 1.614 ms |
| Clock | 230/2000 | 362/2000 | 6.199 / 0.975 ms |

All received samples were ordered and unique. Means describe survivors; lower
joint delay alongside worse delivery is not a successful lossless result.
Native joints and efforts were 500/500. Native latest clock receipts were
1899/2000 and 1920/2000 stepped for m5 and DMA, respectively. Camera/clock
coalescing and application depth-1 sensor queues remain active in this policy.
Effort publication cohorts differ slightly at the measurement-window edges.

The m5 image case took 4227.4 wall seconds and DMA 4811.3 under shared load.
The default transport remains m5; DMA is an explicitly selected backend with a
strong standalone control result and limited image improvement.

## Detailed-CPU lossless stereo result

Both backends receive every actual publication with no duplicates or reordering:
60/60 frames per camera, 60/60 complete pairs, 500/500 joints, 2000/2000 clocks,
and 501/501 m5 or 499/499 DMA effort commands. The effort cohorts differ at
the measurement-window edges; both native modes publish and receive 500/500.
Native images, joints and clocks also arrive completely in both modes.

| Metric | m5 | DMA | DMA matched stepped native |
| --- | ---: | ---: | ---: |
| Complete-pair callback delay, mean | 26.251 ms | 24.419 ms | 0.300 ms |
| Complete-pair age, mean | 26.918 ms | 25.085 ms | 0.633 ms |
| Complete-pair age, p99 / max | 36.053 ms | 38.886 ms | 1.333 ms |
| Joint callback delay, mean / p99 | 15.169 / 34.335 ms | 11.583 / 29.885 ms | 0 / 0 ms |
| Effort callback delay, mean / p99 | 8.298 / 25.209 ms | 8.076 / 23.145 ms | 0 / 0 ms |
| Clock callback delay, mean / p99 | 15.822 / 36.681 ms | 12.097 / 31.555 ms | 0.088 / 1 ms |

DMA's matched native-free mean pair age is 0.917 ms; m5's native pair ages are
0.567 ms stepped and 0.667 ms free-running. Native zero values are the same
1 ms clock bucket. All native per-stream references remain in the export.

Mean pair callback delay falls by 7.0%, with better mean joint/clock delay,
but the worst observed pair age increases. Both maxima exceed the 33.3 ms
camera period. With one 60-pair run per backend, these tails do not establish a
stable difference. Retaining every stream costs more than latest delivery:
DMA pair age rises from 23.661 to 25.085 ms, effort delay from 1.614 to 8.076 ms,
and clock delay from 0.975 to 12.097 ms. The latest figures describe survivors;
the lossless case retains all actual publications.

The m5 and DMA lossless cases take 5986.1 and 5971.9 wall seconds under shared
host load. These are execution records, not isolated simulator speed ratios.
Endpoints freeze traffic records after reaching the configured drain cutoff;
buffered telemetry export can advance simulation further. ROI instruction
statistics include that export.

## Where an image's time goes

The first detailed diagnostic joins device and bridge traces to one complete
measured stereo capture. FIFO attribution is allowed only after every source
capture has one ordered receipt per camera and the bulk DMA count/size agrees.
Independent trace throttles leave two other measured captures incomplete;
their phases are not combined with this capture. This is an instrumented
example, rather than an estimate of every frame's distribution.

| Interval | Console example, ms | RAM example, ms |
| --- | ---: | ---: |
| Actual source publication → descriptor header ready | 0.000060 | 0.000060 |
| Descriptor/SG setup → payload start | 0.000816 | 0.000816 |
| Coherent payload DMA, 1.84 MB stereo packet | 0.162951 | 0.162592 |
| DMA completion → IRQ acknowledgement | 0.002132 | 0.004204 |
| IRQ acknowledgement → bridge receipt | 0.071390 | 0.116453 |
| Bridge view setup and diagnostic output | 0.182305 | 0.031305 |
| Bridge ROS publication calls and diagnostic output | 18.995675 | 21.086575 |
| Publication return → later camera callback | 3.872890 | 3.382988 |
| Actual publication → later camera callback | **23.288218** | **24.784993** |

The reconstruction agrees with the observed callback timestamp within rounding.
Scheduled-capture age is 24.622 ms because source publication was 1.333 ms late.
Completion-to-ACK includes interrupt delivery and kernel completion handling;
ACK-to-bridge includes worker scheduling and parsing. Descriptor fetch precedes
`HEADER`. A receiving callback may overlap publication on the second core.
The console example is capture 66,666,667 ns; the RAM example is capture
400,000,000 ns. RAM capture publication was on time, so its age equals its
24.785 ms callback delay.

The first Linux-timer-based RAM exporter produced no file, despite completing
the ROS run; it provides no bridge attribution. The corrected diagnostic waits
for origin creation with inotify and checks gem5 elapsed time before exporting
at 703.678 ms, after the 350–550 ms source window. It preserves every core
runtime binary and confirms matching host/guest Fast DDS 2.6.12,
`rmw_fastrtps_cpp` 6.2.10 and `rclcpp` 16.0.21 package versions. The helper
adds modeled work and the two diagnostic warmups differ, so subtracting their
timings would not isolate console cost.

The RAM diagnostic receives all 6/6 measured pairs, with mean pair callback
delay 25.786 ms and age 26.452 ms, versus matched stepped-native 0.167/0.500 ms
and native-free 1.500/2.000 ms. It receives 43/50 joints, 23/50 efforts and
31/200 clock messages. These are diagnostic observations under latest QoS,
not an additional performance acceptance result. Of three traced measured
captures, one has every stage; incomplete captures remain separate in JSON.

This example places less than 1% of the image callback delay in payload DMA.
It locates most time inside ROS publication and subsequent delivery; it does
not yet distinguish Fast DDS internal copies, locks and scheduling. Reducing
DMA latency further cannot remove the approximately 19 ms publication call.

A separate six-pair pilot raises the DDS message limit to 4 MiB and enables
only SHM, with the same 16 MiB segment. Fast DDS 2.6 chooses the minimum
message limit across enabled transports; keeping default UDP retains its
65,500-byte limit. An independent probe against the installed middleware
verifies that the experimental XML loads one SHM transport, disabled builtins,
the 4 MiB limit and 16 MiB segment. Ordinary runtime binaries are unchanged.
([Fast DDS transport selection](https://github.com/eProsima/Fast-DDS/blob/v2.6.12/src/cpp/rtps/network/NetworkFactory.cpp#L127-L136),
[Fast DDS transport XML](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/xml_configuration/transports.html))

That pilot receives all 6/6 pairs with mean callback delay 24.291 ms for m5 and
23.943 ms for DMA. Their respective matched stepped-native means are 0.167 ms
and zero clock buckets; native-free means are 1.000 and 0.667 ms. Joint receipts
are 45/50 for each backend, effort receipts 22/50 and 22/51, and clock receipts
21/200 and 35/200. A prior equal-window DMA pilot with the original 16 MiB
UDP/SHM profile measured 20.662 ms, also 6/6 pairs. These small, single-repeat
pilots provide no evidence that raising the DDS message limit solves the
image delay. The SHM-only adapter remains an explicit experiment; it cannot
communicate with remote DDS participants. Actual RTPS fragment counts were
not measured, so the settings check alone does not prove the packetization.

Its additional 1280×960 / 10 Hz full-byte diagnostic receives the single
measured 7.37 MB stereo pair intact, with 194.006 ms age, plus 1/1 joint and
100/100 clock samples. It has **zero measured effort publications** within its
100 ms source window; both native modes publish one. Strict export of all nine
experiment cases correctly rejects that empty source. The six performance
cases are exported separately with verified native controls; all nine attempts
remain in diagnostic history. This large-image check does not establish
mixed-stream performance or sustained 10 Hz delivery.

## Correctness and verification

- Nine transport CTests pass, including real-controller driver-boundary mocks,
  bounded batching, whole-packet validation, shared slices, held RX loans,
  staging starvation, persistent malformed failure and external-loan shutdown.
  All nine also pass under ASan/UBSan.
- Fourteen ROS/package CTests pass, including guard notifications before and
  during executor wait, lossless stereo pairing, manifest compatibility,
  DDS configuration, metrics and guest reuse compatibility. Sixty-four deployed
  configuration checks pass. Nine analysis/collector checks pass.
- The final detailed-CPU host-mailbox probe passes all eighteen byte/dirty-guard
  checks at 0, 63, 64, 4096, 4097, 921600, 1843200, 1843337 and 7372800 bytes,
  twice each. Invalid requests, exclusive open and reset/reopen with DMA in
  flight pass. Its own native two-copy reference runs first on the same host.
- Full ROS byte checking changes capture markers at both ends of every image
  and checks the entire body. These runs are separate from performance because
  scanning pixels adds modeled guest work.

At 640×480 / 30 Hz, the full-byte case received all 6/6 images per camera and
6/6 complete pairs, without corruption, duplicates or reordering. The added
pixel scan produces pair age of 46.6 ms on average: only 131/200 measured clock
messages arrived, consistent with backlog exceeding bounded depth-128 queues,
versus 200/200 in both native controls. Joints were 50/50 and effort commands 49/49 actual
publications. This validates image bytes and buffer lifetime; it does **not**
establish lossless mixed-stream performance under pixel-scanning load. The
performance matrix disables pixel scanning and reports its own complete counts.

The larger full-byte case uses 1280×960 RGB at 2 Hz, 10 Hz joints/control and
1 kHz clock. All 2/2 measured frames per camera and both 7.37 MB stereo pairs
arrived without corruption, duplicates or reordering. Joints and efforts were
10/10 each; clock receipts were 975/1000, versus 1000/1000 in both native modes.
With pixel scanning enabled, complete-pair age averaged 128.36 ms (145.40 ms
maximum), versus native-free 15 ms and stepped native 0 ms clock buckets.
This establishes large-packet correctness at 2 Hz. The separate large
bidirectional case disables pixel scanning to measure the complete ROS path.

The latter delivered both measured 7,372,800-byte messages in each direction
at 2 Hz. Mean publication-to-callback delay was 143.30 ms host-to-guest and
222.23 ms guest-to-host, versus matched native-free 9.5 ms in each direction
and stepped-native zero clock buckets. This generic byte-array case checks
size, header and sequence; full-body integrity is established separately by
the device guards and typed stereo pixel scans. Large-message delivery works,
but the complete ROS path still has substantial overhead.

## Next performance targets

Prioritize the image publication path. Isolating control/clock callbacks from
image publication would address head-of-line blocking; it must be tested for
delivery and causality under the same mixed workload. A more radical experiment
would DMA into reusable typed image storage and compose the bridge with the
guest consumer, transferring unique ownership through rclcpp intra-process
communication. Humble's image-pipeline example demonstrates that ownership path.
Keeping ordinary ROS Image topics for outside subscribers requires a copying
fallback. These are proposed experiments, not capabilities established here.
([Humble intra-process image example](https://github.com/ros2/ros2_documentation/blob/humble/source/Tutorials/Demos/Intra-Process-Communication.rst))

Directly targeting existing Image vectors would require proper user-page DMA
registration, scatter/gather handling and reset-safe lifetime tracking; the
current driver instead owns its coherent pool. Linux requires the appropriate
page-pinning and unpinning APIs for that design. Merely enabling middleware
loaning does not establish zero-copy for an Image containing strings/vectors;
Humble documents the POD restriction for the demonstrated Fast DDS loan path.
([Linux DMA page pinning](https://docs.kernel.org/5.15/core-api/pin_user_pages.html),
[Humble loaned-message constraints](https://github.com/ros2/ros2_documentation/blob/humble/source/How-To-Guides/Configure-ZeroCopy-loaned-messages.rst))

Guest source release lag and the completed-boundary delay for outgoing controls
remain separate targets. Validate timer behavior on both guest cores before
changing the clock model. Any finer synchronization or event-triggered host
delivery must preserve causal ordering and retain both native comparisons.

## Reproduction and evidence

The exact matrices are retained with the measurements. Run one session per
socket namespace, with read-only disk/kernel mounts and the exact kernel module:

```bash
python3 profiling/chimaera_latency/run_cases.py dma-cases.json OUTPUT \
  --runtime RUNTIME --bundle GUEST_BUNDLE --gem5-root GEM5_SDK \
  --source-snapshot SOURCE_ARCHIVE
python3 profiling/chimaera_rewrite/collect_integrated.py M5_OUTPUT DMA_OUTPUT \
  --physical-host ubuntu-george --output RESULTS
python3 profiling/chimaera_rewrite/render_integrated.py \
  RESULTS/attempts.json RESULTS/report.html
```

The retained measurement directory separates acceptance, validation and DDS
experiment exports. Its profile JSON files preserve individual DMA intervals,
capture joins and reconstruction checks. `verification.json` records test
commands, build settings, binary/source hashes and unsuccessful diagnostics.
The DDS adapter requires its explicit reproduction command in the profiling
README; the normal case runner does not interpret the experiment-only fields.
Acceptance contains eighteen matched cases and 72 stream rows; validation
contains eighteen matched cases and 81 stream rows. The separate DDS pilot
contains six matched performance cases and three diagnostic attempts. All
45 planned measurement attempts completed. The integration history retains
102 ROS attempts: 92 complete, seven failed, two cancelled and one interrupted.
Historical failures are retained rather than interpreted as measurements from
the final runtime.

Use the retained source archive and binary hashes to reproduce measurements.
The measured runtime/model sources match `cfc5dc1`; a later test-only change is
included in the commit. The final simulator SHA-256 is
`705431873db47fc80242b5e07609de983c5c4532b5dc3d0e4f58e450bf64dde4`.
The guest module SHA-256 is
`a276f70f4e878c2c0ccf362f43343f84fdcafeea6c68d4abbc52f437c24e7fdc`.
Raw cases, immutable runtime/bundle/source snapshots, logs and all failed or
cancelled attempts remain under `~/Chimaera/.chimaera-rewrite/20261020` on
`ubuntu-george`; compact copies are retained locally for analysis.
