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
cost. Host wall times are recorded under shared machine load and container
limits; they are not isolated simulator speed ratios.

Callback delay runs from actual source publication to the receiving callback.
Age runs from scheduled source release, so Linux timer and scheduling delay
cannot disappear from the measurement. Pair age ends at the later camera
callback. Delivery counts, duplicates, ordering and source release lag accompany
latency. Sixty pairs improve on six-frame pilots but give limited evidence for
rare latency tails.
These deltas are guest-visible/co-simulation time. Work on the host while gem5
is paused leaves that clock unchanged. Native-stepped callbacks can complete
inside a paused physics-clock bucket, so its sub-millisecond means are averages
of quantized observations. Native-free values are retained alongside them.

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

## Correctness and verification

- Nine transport CTests pass, including real-controller driver-boundary mocks,
  bounded batching, whole-packet validation, shared slices, held RX loans,
  staging starvation, persistent malformed failure and external-loan shutdown.
  All nine also pass under ASan/UBSan.
- Fourteen ROS/package CTests pass, including guard notifications before and
  during executor wait, lossless stereo pairing, manifest compatibility,
  DDS configuration, metrics and guest reuse compatibility. Sixty-four deployed
  configuration checks pass. Eight analysis/collector checks pass.
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

## Reproduction and evidence

The exact matrices are retained with the measurements. Run one session per
socket namespace, with read-only disk/kernel mounts and the exact kernel module:

```bash
python3 profiling/chimaera_latency/run_cases.py dma-cases.json OUTPUT \
  --runtime RUNTIME --bundle GUEST_BUNDLE --gem5-root GEM5_SDK \
  --source-snapshot SOURCE_ARCHIVE
python3 profiling/chimaera_rewrite/collect_integrated.py M5_OUTPUT DMA_OUTPUT \
  --physical-host ubuntu-george --output RESULTS
```

Use the retained source archive and binary hashes to reproduce measurements.
The measured runtime/model sources match `cfc5dc1`; a later test-only change is
included in the commit. The final simulator SHA-256 is
`705431873db47fc80242b5e07609de983c5c4532b5dc3d0e4f58e450bf64dde4`.
The guest module SHA-256 is
`a276f70f4e878c2c0ccf362f43343f84fdcafeea6c68d4abbc52f437c24e7fdc`.
Raw cases, immutable runtime/bundle/source snapshots, logs and all failed or
cancelled attempts remain under `~/Chimaera/.chimaera-rewrite/20261020` on
`ubuntu-george`; compact copies are retained locally for analysis.
