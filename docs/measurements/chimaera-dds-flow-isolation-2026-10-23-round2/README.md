# Creation timestamps and two-core robot recovery: round 2

[Interactive report](report.html), [full evidence](summary.json), [per-stream CSV](metrics.csv).

The round has one absolute two-hour host wall-time cap, including preparation and recovery. The first round remains separate. All cases use fresh COW guests; base images are unchanged.

All owned simulations were stopped and checked at 2026-10-23T19:32:58.543814+00:00. Elapsed round time: 6958.5 / 7200 seconds.

## Actual robot recovery

The retained guest rejects newer trace manifest fields. The recovery adapter explicitly disables tracing and removes only its three unsupported fields, preserving transport and application settings. The retained three-process application then boots but does not reach readiness within 600 seconds.

The opt-in `composed:=true` candidate puts the three robot nodes in one process/context, with one executor for effort control and another for perception/planning. Physics, gains, rates and algorithm source are preserved. It catches and retains the ball on the fixed two-core geometry. The wrapper subsequently fails importing the newer tracing postprocessor; offline recovery uses the saved score, telemetry and statistics without rerunning or replacing them.

- actual-robot-kvm-r1: [gem5.opt-5] RuntimeError: guest finished before its bridge was ready: m5_fail instruction encountered (code 2); see /work/.chimaera-m5/isolation-20261023-r2/actual-robot-kvm-r1/baseline/gem5/board.pc.com_1.device
- actual-robot-kvm-r2: [gem5.opt-5] RuntimeError: guest finished before its bridge was ready: m5_fail instruction encountered (code 2); see /work/.chimaera-m5/isolation-20261023-r2/actual-robot-kvm-r2/baseline/gem5/board.pc.com_1.device
- actual-robot-kvm-r3: No scored result
- actual-robot-kvm-r4: [gem5.opt-5] RuntimeError: Build gem5 with RAMULATOR2_ROOT; see gem5/ext/ramulator2/README.md
- actual-robot-kvm-r5: {'success': True, 'reason': 'inside_cup_at_deadline', 'launch_time': 0.206, 'result_time': 1.706, 'duration': 1.5, 'contained_duration': 0.5315, 'flight_end_time': 1.1745, 'flight_end_kind': 'cup_capture'}

The successful catch launches at 0.206 s and scores at 1.706 s, with 0.5315 s inside the cup. Its first effort receipt is 0.0805 s; the failed retained three-process attempt first delivers effort at 0.5405 s. These are KVM diagnostic observations, not TimingSimpleCPU performance claims. The native composed smoke test reports 13 threads and about 31 MiB RSS without sensor traffic; guest resource qualification remains separate.

## Creation boundary and timing comparison

`creation_ns` is recorded before logical per-sample preparation. Payloads are preallocated and reused, so initial allocation and physical sensor capture remain outside that timestamp. Creation-to-callback includes preparation, publication/serialization, bridge transport, reception/deserialization and the wait for callback execution. The report also retains publish-entry latency, scheduled-release age and missing source releases. Historical creation timestamps are never invented.

Guest timestamps use m5 simulation time. Host timestamps use the logical clock at completed coupling boundaries; host computation while gem5 is paused consumes zero simulation time in both modes. Timestamp consistency therefore does not calibrate physical host computation or offload execution costs.

The callback timestamp follows a lifecycle guard. Buffered recording, locking and clock queries add modeled guest work; their observer effect has not been isolated. Both arms use identical instrumentation, but the results remain instrumented diagnostics rather than an uninstrumented callback-latency guarantee.

Both copy modes preload the identical helper and share the same simulator, endpoint, DDS library, pools, QoS and two-core geometry. Only the helper mode changes. Functional mode excludes large scoped middleware copy/fill costs; application computation, prefault stores and remaining middleware stay modeled. An intended physical offload implementation needs its operation costs represented or calibrated.

The KVM matrix verifies full image bodies and timestamp coverage in four cases. Timing uses 0.05 s warmup, 0.2 s source and 0.1 s drain, with one measured 8 MiB sample per direction under load. Two interleaved unloaded repetitions per mode precede the loaded pair; a third reference follows if the cap permits. About 50 joint/effort samples means their p99 equals the observed maximum; these windows do not establish a statistical tail guarantee.

Warmup bulk transfers may finish inside the measured receipt window. The CSV separates receipts from earlier publications from receipts published in the measured source window. Fixed-window goodput and finite publication-cohort delivery remain distinct; neither demonstrates sustained capacity in these short transients.

### KVM

- copies-ball-guest-kvm-r1: complete
- copies-ball-functional-kvm-r1: complete
- copies-bulk8m-guest-kvm-r1: complete
- copies-bulk8m-functional-kvm-r1: complete

### TIMING

- copies-ball-guest-timing-r1: complete
- copies-ball-functional-timing-r1: complete
- copies-ball-guest-timing-r2: complete
- copies-ball-functional-timing-r2: complete
- copies-bulk8m-guest-timing-r1: complete
- copies-bulk8m-functional-timing-r1: complete
- copies-ball-guest-timing-r3: complete
- copies-ball-functional-timing-r3: complete

| Mode / load | Joint / effort / clock creation p99 | Left / right creation max |
|---|---:|---:|
| guest / ball r1 | 0.778 / 1.897 / 1.764 ms | 5.574 / 9.648 ms |
| functional / ball r1 | 0.408 / 1.869 / 0.374 ms | 0.258 / 0.314 ms |
| guest / ball r2 | 0.367 / 1.695 / 0.546 ms | 6.467 / 9.846 ms |
| functional / ball r2 | 0.722 / 1.990 / 0.746 ms | 0.248 / 0.335 ms |
| guest / bulk8m r1 | 4.010 / 2.127 / 3.167 ms | 84.472 / 86.561 ms |
| functional / bulk8m r1 | 0.420 / 1.282 / 0.532 ms | 0.768 / 0.835 ms |
| guest / ball r3 | 2.832 / 3.662 / 3.621 ms | 4.783 / 6.834 ms |
| functional / ball r3 | 0.291 / 1.304 / 0.405 ms | 0.536 / 0.596 ms |

## Additional checks within the same cap

Timing/Ruby full-byte integrity: pass.
Burst-phase sensitivity: not_run.
Actual composed functional-copy qualification: not_run.

## Remaining scope

- Logical creation means per-sample preparation start with preallocated payloads; not physical capture or initial allocation.
- Functional-copy mode excludes scoped copy/fill costs; its timing model is reported separately.
- Host timestamps use the logical coupling clock; host computation while gem5 is paused has zero simulation-time cost in both modes.
- Actual robot recovery is KVM correctness, not timing-model latency evidence.
- The actual robot uses 0.5 ms coupling/physics and 1 kHz joint feedback; the synthetic timing diagnostic uses 2 ms coupling and 250 Hz feedback.
- No numerical regression allowance, sustained-capacity claim or full per-flow independence is established.
- Guest RSS and thread counts under robot sensor traffic are not measured by the native smoke test.
- Buffered tracing adds guest work and shared locking; the callback marker follows a lifecycle guard. Observer overhead has not been isolated.
- Robot startup observations use single KVM attempts; the newly compiled candidate also contains current feedback diagnostic logging absent from the retained executable.

Accepted delivery contract: stereo capture pairs, joint feedback, effort commands, clock and complete replacement trajectories use latest-sample delivery. Benchmark records are lossless. At recorder overload, record pressure may delay its producing callback, including control; those waits are a separately reported exception to the latency target. Unrelated producers still require isolated progress.

Per-topic priorities, mixed lossless/latest admission, reserved flow credits and sustained stress qualification remain outstanding. The composed application is an opt-in execution candidate, and the synthetic endpoint still groups camera/bulk work on one executor. The rebuilt Ramulator-enabled functional-copy simulator is separately prepared and instantiated at tick zero; that preparation alone is not an actual functional-copy robot pass. Any later actual run and its copy-counter qualification are reported separately above.

Raw evidence and frozen selections: `.chimaera-m5/isolation-20261023-r2/`.
