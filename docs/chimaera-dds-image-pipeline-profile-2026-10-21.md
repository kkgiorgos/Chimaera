# DDS image pipeline profile, 2026-10-21

The KVM investigation locates the largest image delays **after the DMA device has completed**: interrupt/completion service, the bridge-to-application IPC hop, and DDS/ROS executor service. In the complete 2 ms, verification-disabled trace, those stages account for 14.76 ms of the right image's 17.18 ms mean. Typed deserialization itself accounts for 0.29 ms. Each image crosses as one bulk packet; fragmentation is not the explanation in this configuration.

These are measurements of the instrumented KVM path. The earlier **20.704 ms stereo-pair mean** in [the middleware comparison](chimaera-dds-middleware.md) used TimingSimpleCPU. No new TimingSimpleCPU or O3 run was performed. KVM cannot provide an exact attribution of that historical result: it uses `atomic_noncaching` memory and records effectively instantaneous payload DMA. The stage percentages below must not be applied to the historical 20.704 ms.

The [interactive report](measurements/chimaera-dds-image-pipeline-2026-10-21/report.html) switches between cameras, cases, and mean/worst-image attribution. [Analysis JSON](measurements/chimaera-dds-image-pipeline-2026-10-21/analysis.json), [comparison CSV](measurements/chimaera-dds-image-pipeline-2026-10-21/comparison.csv), [raw evidence](measurements/chimaera-dds-image-pipeline-2026-10-21/raw-evidence.tar.gz), and [attempt history](measurements/chimaera-dds-image-pipeline-2026-10-21/attempt-history.json) retain the supporting records.

## What was measured

- The same ball-catching traffic preset as the earlier comparison: two 480 × 360 RGB cameras at 30 Hz, 250 Hz joints and effort commands, and a 1 kHz clock. Each image contains 518,400 pixel bytes; serialized left/right images contain 518,464/518,468 bytes.
- Reliable depth-128 endpoints, the same 16 MiB SHM-only local DDS configuration with a 1 MiB message limit, DMA backend, and the matching guest kernel/module. This is the benchmark's lossless policy, rather than the application's sensor QoS.
- A 0.25 s warmup, 0.5 s measured source window, and 0.5 s drain. Each completed case has 15 measured images per camera. The full source period produces 23 images per camera, including warmup.
- Native free-running and native stepped controls; frozen-runtime KVM controls; full tracing with payload verification on/off; 1/2/10 ms coupling; a four-core check; and a 100 µs KVM event-queue quantum experiment. Default KVM geometry is two cores. All newly simulated guest CPUs are `X86KvmCPU`.
- Publication-to-callback latency uses the original endpoint timestamps. The profiler identifies images using their CDR frame name and capture timestamp without adding a wire envelope, hashing pixels, or changing message size. Records are buffered and exported after measurement/drain.

This profiles the **message publication → receiving callback** pipeline used to establish the transport comparison. Images are preallocated by the traffic generator. Gazebo rendering, camera-to-ROS conversion, robot perception and planning are outside this benchmark, so their execution time is not included.

## Complete additive breakdown

Case: `kvm-trace-verify0`, two cores, 2 ms coupling, default 1 ms KVM quantum. Verification is disabled to expose the transport/dispatch path without the synthetic full-pixel check. All 30 measured images have unique, complete, causally ordered joins. Every row below is an elapsed interval, including any intervening work, queueing, scheduling and profiler overhead; a row is not a pure CPU-time measurement.

| Interval | Left mean, ms | Right mean, ms | Boundary being measured |
| --- | ---: | ---: | --- |
| Publish → serializer | 0.000 | 0.000 | Original publish timestamp → typed serializer entry |
| CDR serialization | 0.000 | 0.000 | Typed serialization begin → end |
| DDS packet construction / send entry | 0.000 | 0.000 | Serialized payload → custom transport send entry |
| Host IPC / parsing | 0.000 | 0.000 | Transport send entry → complete packet accepted by host router |
| Host router / boundary queue | 0.000 | 0.000 | Router acceptance → coordinator takes packet |
| Host controller / mailbox copy | 0.000 | 0.000 | Router take → host commits DMA mailbox |
| Admission → DMA header | 0.722 | 2.415 | Mailbox commit → device descriptor header |
| DMA descriptor setup | <0.001 | <0.001 | Descriptor header → payload begin |
| DMA payload transfer | 0.000 | 0.000 | Payload begin → device completion, in KVM |
| IRQ / driver / completion thread | 2.094 | 2.641 | Device completion → guest completion worker reads completion |
| Guest bridge scheduling | 1.129 | 1.311 | Completion worker timestamp → bridge services received DMA frame |
| DMA decode / bridge exchange | 1.177 | 2.846 | Frame service → guest router receive entry |
| Guest router / IPC | 3.015 | 3.227 | Router receive entry → application transport has complete packet |
| DDS receive / reader history / executor | 6.174 | 4.367 | Application transport receipt → typed deserialization entry |
| Typed deserialization | 0.375 | 0.290 | CDR deserialization begin → end |
| rclcpp dispatch → callback | 0.068 | 0.083 | Deserialization end → original callback timestamp |
| **Publish → callback** | **14.755** | **17.180** | **Exact sum before display rounding** |

The host rows have zero **simulation-time** delay because the logical host clock is frozen while those operations execute. They still consume wall time. For the right camera, their measured mean wall intervals are 0.117 ms serialization, 0.251 ms DDS/wire construction, 1.720 ms host IPC/parsing, **93.217 ms in the host router/boundary queue**, and 1.175 ms controller/mailbox processing. The left router/boundary interval is 94.356 ms wall time. These host wall intervals must not be added to the simulation-time table or subtracted from guest Linux monotonic timestamps.

The right image's conservative DDS/executor interval can be inspected further: RTPS receive processing averages 0.694 ms, and receive-return to deserialization averages 3.673 ms. Those subintervals can overlap in other cases because receive and executor threads run concurrently; the additive table keeps their combined interval. Similarly, IRQ acknowledgement and completion-thread subintervals are retained as diagnostics, including negative values caused by cross-core timestamp skew, rather than being clamped into an apparently precise partition.

The left path has 95.1% of its traced mean after device completion; the right path has 85.9%. The dominant intervals therefore concern guest delivery and service, beyond placing the payload in guest memory.

## What makes the slow images slow

The slowest right image in the main verification-disabled trace is capture **500 ms**, with **30.999 ms** publish-to-callback delay:

| Component | Delay, ms |
| --- | ---: |
| Admission / descriptor | 3.000 |
| IRQ / completion service | 4.454 |
| Bridge scheduling | 5.503 |
| DMA decode / exchange | 0.574 |
| Guest router / IPC | 6.543 |
| DDS / executor service | 10.770 |
| Deserialization / callback dispatch | 0.155 |

The slowest left image in that case also has capture 500 ms. Its 30.757 ms delay includes **19.437 ms** between transport receipt and deserialization. In the verification-enabled pilot, the slowest right image has 31.975 ms delay and **23.541 ms** in DDS/executor service. These are individual observed samples, not stable tail estimates.

The trace does not identify all of that service interval as an executor asleep while a ready sample waits. It includes receiver-side processing/history work, readiness propagation, competing callbacks/source timers, scheduling and the take path. Its location is established; distinguishing those causes more finely requires scheduler/ready-event tracing rather than interpreting the whole interval as `memcpy` or a DDS retransmission timer.

## Copies and handoffs in the implementation

The current path contains at least **eight full-payload userspace copies** in the custom transport/coordinator layer, beyond ordinary typed serialization/deserialization and any Fast DDS history copies:

1. [`wire::packet`](../ros/chimaera_ros_ws/src/chimaera_dds_transport/src/protocol.hpp) copies RTPS bytes into the `CDDS` frame.
2. [`Router::service`](../ros/chimaera_ros_ws/src/chimaera_dds_transport/src/router.cpp) receives host socket chunks into a temporary buffer and inserts them into `client.input`.
3. It constructs a complete packet vector from that input.
4. `accept_packet` copies the packet into the cross-simulator outgoing deque.
5. [`Bridge::take`](../ros/chimaera_ros_ws/src/chimaera_ros_bridge/include/chimaera_ros_bridge/bridge.hpp) allocates a `Message` and copies the router packet into it.
6. [`DmaHostController`](../gem5-transport/src/dma_host_controller.cpp), through `dma::encode`, copies the frame into the host mailbox.
7. On receipt, `Router::receive` constructs a vector from the guest DMA-backed bytes.
8. `forward` copies that vector into the application's client-output queue.

Socket transfers, vector growth/initialization, Fast DDS receive-history ownership and deserialization add further work. Thus DMA loans do not make this end-to-end path zero-copy. Eight copies of pixel bytes alone represent about **249 MB/s of copied payload** for this stereo rate, before counting memory reads plus writes or the additional layers. This is static copy accounting, not an experimentally isolated bandwidth bottleneck.

The receive path also crosses several execution contexts: device/IRQ, completion worker, guest bridge executor, router worker, application transport reader and application executor. The measured intervals span these handoffs. Adding guest cores alone did not remove their cost in the short four-core experiment.

The full source period has exactly one corresponding bulk DMA completion per image: left frames are 518,596 bytes and right frames 518,600 bytes including framing. Their count, size, explicit image IDs and receipt order agree. No image fragmentation or repeated bulk image packet is needed to explain these runs' latency.

## Controls, clock effects and confidence

The full [comparison CSV](measurements/chimaera-dds-image-pipeline-2026-10-21/comparison.csv) includes every completed case. Selected simulation-time results are:

| Case | Left mean, ms | Right mean, ms | Right maximum, ms |
| --- | ---: | ---: | ---: |
| Frozen native-free, verification on | 0.733 | 2.067 | 3.000 |
| Frozen native-stepped, verification on | 0.333 | 0.067 | 1.000 |
| Frozen KVM, verification on | 8.615 | 11.358 | 18.407 |
| Frozen KVM, verification off | 8.699 | 10.988 | 24.921 |
| Corrected tracing-disabled KVM, verification on | 16.415 | 20.292 | 56.413 |
| Corrected tracing-disabled KVM, verification off | 10.848 | 14.263 | 26.812 |
| Traced KVM, verification on, 2 ms | 10.794 | 15.830 | 31.975 |
| Traced KVM, verification off, 2 ms | 14.755 | 17.180 | 30.999 |
| Traced KVM, verification off, four cores | 13.688 | 18.295 | 25.831 |
| Traced KVM, verification off, 10 ms | 11.019 | 16.063 | 30.039 |
| Traced KVM, verification off, 1 ms | 50.985 | 58.221 | 80.794 |
| Traced KVM, verification on, 2 ms, 100 µs quantum | 8.951 | 15.596 | 42.024 |
| Traced KVM, verification off, 1 ms, 100 µs quantum | 39.946 | 42.470 | 71.993 |

All **570 measured images** across the 19 completed cases were received, without duplicates or reordering. One completed case does have a separate delivery limitation: `kvm-gated-off-verify1` receives **476 of 500 clock messages**, leaving 24 censored at drain. Its image results are complete, but it is not a lossless whole-workload acceptance result. Every other completed case receives every published measured stream message. The CSV records image loss, clock loss and whole-workload completeness separately.

Native-stepped zero/below-1-ms results reflect the frozen/quantized simulation clock, rather than zero execution cost. The traced native endpoints have right-image mean **wall** publication-to-recorded-callback intervals of 2.159 ms free-running and 2.693 ms stepped. Guest Linux monotonic/CPU clocks are also quantized; the additive guest partition uses address-mode `m5_rpns`, not those Linux clocks.

The default KVM `sim_quantum` is 1 ms. At 1 ms coupling, the measured backlog rises sharply; two right-image joins have causally inconsistent completion-worker/bridge timestamps (−46 µs and −140 µs), so the analyzer rejects those two partitions while retaining their real callback measurements. The 1 ms case's right latency table uses all 15 endpoint receipts, not the 13 accepted attribution samples. Its effort source produces 124 measured messages versus 125 in the ordinary case; all published messages are delivered. Treat it as a stressed configuration experiment, not a matched throughput improvement.

The 100 µs quantum experiment changes only the host gem5 Python configuration, keeping the guest binaries unchanged. Both experiments have complete, nonnegative image partitions. However, the 1 ms boundary still averages 42.470 ms for the right image, including 15.768 ms from mailbox commit to DMA header and substantial bridge/forwarding backlog. The problematic 1 ms result is therefore not explained solely by the rejected timestamps. At 2 ms coupling and 100 µs quantum, the right mean remains 15.596 ms; its slowest image spends 37.039 ms in DDS/executor service. Neither a smaller synchronization interval nor more cores should be assumed to improve this pipeline from configuration alone.

The synthetic full-image verifier runs **after** its own callback timestamp on the same single-threaded endpoint executor. It can delay the next image or another ready callback. In the verification-enabled pilot, its measured mean callback-record-to-verification-end interval is 2.445 ms for the left image and 4.104 ms for the right. Removing verification did not consistently reduce end-to-end delay across these short runs; it is therefore not justified to assign the entire historical 9.313 ms left/right callback skew to verification from this KVM evidence.

Tracing adds m5 timestamp calls, mutex/recording work and an export-deadline clock read in the guest bridge loop. An early recorder-disabled control still had that deadline read; it is retained under `overlay-off`, explicitly separate from the corrected `gated-off` controls. The corrected verification-enabled control averages **20.292 ms** for the right image, versus 15.830 ms in the traced pilot. Thus instrumentation/scheduling variation is not a simple positive constant that can be subtracted; removing clock probes can also change execution order and increase measured latency. Traced stages must not be subtracted from untraced totals to claim a pure transport CPU cost. The frozen verification-disabled control also overlapped the beginning of the first traced pilot; the main `kvm-trace-verify0` case and subsequent experiments ran without that overlap. With 15 samples per camera, these independent cases do not establish a precise constant instrumentation overhead or stable p95/p99 estimates.

Two original KVM attempts failed the device-access preflight before producing a VM measurement. A later diagnostic-preflight repeat did not complete guest event export and was stopped; its artifacts remain failed. Successful cases are separated from these attempts in the retained history. No failed/incomplete case contributes latency results.

## What to optimize first

1. **Guest delivery handoffs and packet ownership.** A coordinator-to-participant shared-memory ring carrying references to registered packet buffers would target both the extra guest copies and socket/thread handoffs. It must preserve lifetime until DDS has finished using each packet and retain bounded backpressure.
2. **DDS-to-executor service.** Instrument sample-ready, executor wake/take and callback/source-timer execution to split the largest remaining service interval. Keep expensive image work and source generation from blocking the receiving executor where application semantics permit.
3. **IRQ/completion service.** Investigate the 2–4 ms completion path with guest scheduler/interrupt timing. The current trace alone does not establish how much is IRQ handling, run-queue delay or KVM scheduling behavior.
4. **Host wall-time coupling separately.** [`TimingController::settle`](../ros/chimaera_ros_ws/src/chimaera_ros_bridge/include/chimaera_ros_bridge/gazebo_timing_controller.hpp) contains two 20 ms wall-clock settling windows per completed step, alongside Gazebo acknowledgement waits. They explain substantial host wall waiting; they do not directly create a fixed 20 ms simulation-time message delay while the guest is paused.

This work adds diagnostic tools and evidence only. It does not change the production DDS transport, application, guest disk or simulator implementation.

## Reproduction and verification

The [profiling README](../profiling/chimaera_rewrite/README.md#dds-image-pipeline-diagnostics-in-kvm) documents the isolated overlay, runner and analyzer. The retained source archive contains the exact copied bridge, transport and pinned RMW implementation; `profile-source-gated.tar.gz` differs by gating the export-deadline read when tracing is disabled. The raw-evidence archive includes the experiment case lists, actual commands, runtime hashes, clock origins, per-process CSVs, device traces, build logs and export script. [Provenance](measurements/chimaera-dds-image-pipeline-2026-10-21/provenance.json) records SDK/host identity and profiler hashes.

Four analyzer unit tests pass, covering exact reconstruction from the original callback timestamp and rejection of duplicates, missing stages and negative intervals. Two existing DMA parser/FIFO tests pass. The standalone C++ image-identity parser test passes for left/right images inside an outer packet and rejects malformed header lengths/nanoseconds. All 268 accepted real-image partitions reconstruct their original publish-to-callback latency; two inconsistent joins are retained as rejections. Device-to-image FIFO joins check bulk count and byte size before attribution, and have no matching errors. The archive's file hashes and all newly deployed CPU identities were checked.
