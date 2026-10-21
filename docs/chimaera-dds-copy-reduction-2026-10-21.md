# DDS transport copy reduction, 2026-10-21

The custom DDS transport now shares owning packet buffers through its router and
coordinator queues and retains DMA receive loans through socket delivery. On the
host-to-guest image path this removes **seven of the eight full-payload userspace
copies** identified by the [pipeline investigation](chimaera-dds-image-pipeline-profile-2026-10-21.md).
All new workload and regression execution uses **KVM**.

Latency results are mixed. Three matched verification-disabled repetitions show
a **17.2% lower stereo-pair mean**, but the image-byte-verification cohort is
slower and the observed worst-case delay increases. The measurements establish
less copying; they do not establish a uniform latency improvement.

## Implementation

- The participant sends the existing 52-byte CDDS header and the original DDS
  payload with `sendmsg`/two iovecs. Short writes advance through both segments;
  interrupted writes retry, and a failed stream write retires the connection.
- The router reads and validates a complete header before allocating. It then
  receives the payload directly into its final uninitialized allocation. Partial
  headers/bodies and consecutive frames retain their parser state.
- `Packet` copies share a storage owner. Local recipients and the outgoing
  coordinator queue reference the same packet. `Bridge::take()` adopts that
  allocation as a `chimaera::Message`.
- `Bridge::submit()` moves its received message into an owner passed to
  `Router::receive(Packet)`. On the guest this retains the existing DMA loan;
  the slot is reposted after the final socket recipient has accepted the bytes
  or disconnected. Router destruction releases queued owners as well.
- The participant's receive buffer grows without zero-initializing image-sized
  storage and is reused only after synchronous `OnDataReceived` returns, as in
  the previous implementation.

The CDDS and DMA wire formats, simulation-boundary admission, RTPS ordering,
endpoint registration and per-recipient queue accounting are preserved. The
router's raw-pointer `receive(data, size)` overload remains a copying fallback.
Callers must treat a shared packet as immutable after submission. Rebuild the
DDS library and bridge together and restage the guest bundle for the new router
return type.

The remaining image copy on host-to-guest traffic is the host mailbox write.
Guest-to-host large packets retain registered TX staging and the paused host
mailbox read, so their analogous path falls from eight to two copies. This
accounting excludes kernel socket transfers, DDS history, serialization and typed
deserialization. At two 518,400-byte RGB images at 30 Hz, removing seven copies
avoids about 218 MB/s of copied pixel bytes, excluding the corresponding reads
and framing. This is static accounting, rather than a measured bandwidth or
latency saving.

## KVM comparison method

The baseline is the production source at `0e7640b`; the candidate changes only
the packet/transport/router and bridge ownership path. Both were rebuilt in the
same `chimaera:middleware-gem5` SDK. Endpoint, RMW, clock-plugin, DDS XML, DMA
module and simulator hashes match across cases. The guest disk and kernel are
mounted read-only; every attempt uses a fresh ephemeral guest.

The primary matrix has three verification-disabled repetitions per version,
with order baseline/candidate, candidate/baseline, baseline/candidate. A separate
cohort repeats that order with verification of every image byte enabled. No tracing or detailed
CPU runs are added. Configuration:

- Two `X86KvmCPU` cores, default 1 ms event-queue quantum, `atomic_noncaching`
  memory; the retained origin metadata and deployed gem5 configuration establish
  the active CPU classes.
- 480 × 360 RGB stereo at 30 Hz per camera, 250 Hz joint feedback/effort commands,
  and a 1 kHz clock. Reliable depth-128 application endpoints and lossless routing.
- 16 MiB SHM-only local DDS segments and 1 MiB RTPS message limit, DMA ABI 1,
  8 MiB registered slots with 16 RX/four TX slots, 2 ms coupling/1 ms physics.
- 0.25 s warmup, 1 s measured source window and 0.75 s drain. Each case publishes
  30 measured images per camera. Source counts and actual guest command counts
  are retained; completeness means every published message was received.

Publication-to-callback delay includes transport, synchronization, scheduling,
DDS processing and executor service. Full-image verification runs after its
callback timestamp and can delay later callbacks. That matched cohort is reported
separately from the performance repetitions. KVM does not model detailed CPU or
memory execution cost, and these short windows cannot establish stable tails or
isolate memcpy cost.

## Verification-disabled performance repetitions

All three candidate runs improve the stereo-pair mean relative to their matched
baseline repetition. Across 90 measured pairs per implementation, the mean falls
from **20.283 to 16.788 ms (17.2%)**. Left-image mean falls 11.1%; right-image mean
falls 17.2%. Every measured image and every other published measured message is
delivered, with no duplicates or reordering.

| Stream | Baseline mean, ms | Candidate mean, ms | Change |
| --- | ---: | ---: | ---: |
| Left image | 16.552 | 14.722 | −11.1% |
| Right image / complete stereo pair | 20.283 | 16.788 | −17.2% |
| Joint feedback | 19.664 | 13.538 | −31.2% |
| Effort command | 6.004 | 5.245 | −12.6% |
| Clock | 29.916 | 20.015 | −33.1% |

The mean improvement does **not** establish a tail improvement. The candidate's
worst observed stereo-pair delay is **81.880 ms**, versus **60.834 ms** for the
baseline. Per-run pair results retain this variability:

| Repetition | Baseline mean, ms | Candidate mean, ms | Baseline p95, ms | Candidate p95, ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 20.330 | 18.528 | 45.641 | 58.841 |
| 2 | 22.442 | 18.372 | 52.161 | 46.041 |
| 3 | 18.077 | 13.465 | 36.811 | 34.006 |

These are independent distributions, rather than individually paired messages.
Means across repeats are weighted by receipt count; per-run p95 values are not
averaged into an invented pooled percentile. The source windows contain only 30
pairs each. Guest scheduling and DDS/executor handoffs remain material, consistent
with the earlier pipeline profile. Reducing the userspace copy chain does not
remove those handoffs.

Each implementation publishes and receives 90 images per camera, 750 joint
messages and 3,000 clock messages across these repetitions. The guest publishes
742/749 effort commands for baseline/candidate (247.3/249.7 Hz); all are received.
The configured command rate is 250 Hz, and actual source counts remain explicit.
Mean case wall time is 112.60/113.04 s respectively; the changes do not improve
this coupling-dominated wall time.

## Verification-enabled repetitions

The repeated byte-verification cohort regresses overall: mean pair delay rises
from **14.945 to 20.045 ms (+34.1%)**. Every pixel is checked, and all 90 measured
pairs per implementation are delivered without corruption, duplicates or
reordering. Other stream means also rise: joints 11.925 → 16.927 ms, efforts
4.339 → 6.592 ms, clock 16.683 → 23.978 ms.

| Repetition | Baseline pair mean, ms | Candidate pair mean, ms |
| --- | ---: | ---: |
| 1 | 11.032 | 18.119 |
| 2 | 14.489 | 28.028 |
| 3 | 19.314 | 13.990 |

The observed pair maximum increases from 51.861 to 59.947 ms. Actual effort
source counts are 749/748 for baseline/candidate; every published command is
received. Host source counts match exactly: 90 images per camera, 750 joints
and 3,000 clocks per implementation.

The third repetition improves while the first two regress. The data therefore
show a workload-dependent outcome and substantial run-to-run variation under KVM.
The post-callback verifier competes with later transport/dispatch work; these
measurements do not isolate that competition or prove why its ordering changes.
The optimization reduces copying, but its timing benefit should not be assumed
for a CPU-loaded application from the verification-disabled result alone.

## Ownership and reduced-pool validation

The router regression executable passes inside KVM before the first candidate
endpoint starts, and again in a separate reduced-pool run. It covers boundary
admission, byte integrity, malformed/wrong-side ingress, persistent overflow
failure, endpoint lifecycle, split headers/bodies, consecutive frames, shared
loan lifetime, fanout, slow-recipient disconnect and scatter/gather delivery.
Successful endpoint startup behind the recorded `bash -ec` guard establishes
that the regression exited successfully; the serial log can remain partially
buffered at managed shutdown.

The extra run uses four RX slots and the minimum two TX slots, with the same
8 MiB slot size and full image verification. It receives all 30 images per
camera, 250 joints, 247 published efforts and 1,000 clocks. Every pixel matches;
there are no missing, duplicated or reordered messages. Its pair mean is
20.081 ms. This is a correctness check with a different pool geometry, outside
the matched latency comparison.

In total, **13 completed KVM cases deliver all 780 measured images**, including
420 with every image byte verified. No native unit/integration executable or
TimingSimpleCPU/O3 simulation was run. C++ formatting and whitespace checks pass.
One additional attempt, using an extra preflight log exporter, fails during
guest startup before measurement. Its cause was not isolated. The retry with
the original guarded startup and unchanged runtime passes with the same reduced
pool. Both attempts remain in the evidence; the failed startup contributes no
latency samples.

The [interactive report](measurements/chimaera-dds-copy-reduction-2026-10-21/report.html),
[comparison JSON](measurements/chimaera-dds-copy-reduction-2026-10-21/comparison.json),
[CSV](measurements/chimaera-dds-copy-reduction-2026-10-21/comparison.csv),
[reduced-pool summary](measurements/chimaera-dds-copy-reduction-2026-10-21/bounded-summary.json),
and [provenance](measurements/chimaera-dds-copy-reduction-2026-10-21/provenance.json)
retain complete counts, configurations, source and artifact hashes. Compressed
raw evidence includes all attempt records, events, commands, deployed gem5
configuration, CPU origins, environment selection and build logs. Generated
readfiles containing repeated base64 bundles are omitted; their inputs and
bundle hashes are retained.

## Reproduction

Build both versions in Release mode with the pinned RMW overlay sourced; include
`dds_router_test` in the candidate runtime's `lib/chimaera_ros_bridge` directory
before staging. `copy_router_tests: true` in a case executes it inside that KVM
guest before starting the endpoint. The shell uses `-e`, so a failed regression
prevents the endpoint from starting. The extra bounded-pool check uses the same
guard and ordinary measurement/export path.

```sh
python3 profiling/chimaera_rewrite/dds_large_messages.py \
  profiling/chimaera_rewrite/run_copy_cases.py CASES.json RUNS \
  --runtime CANDIDATE_RUNTIME --bundle CANDIDATE_BUNDLE \
  --original-runtime BASELINE_RUNTIME --original-bundle BASELINE_BUNDLE \
  --gem5-root GEM5_ROOT --image READ_ONLY_IMAGE --kernel MATCHING_KERNEL
python3 profiling/chimaera_rewrite/summarize_copies.py RUNS RESULTS
python3 profiling/chimaera_rewrite/render_copies.py \
  RESULTS/comparison.json RESULTS/report.html
```

The runner rejects non-KVM or native cases and prefixes each selected runtime's
library path, so the baseline cannot accidentally load the candidate DDS
library. The exporter checks CPU provenance, configuration matching and shared
artifact hashes before comparing results. It retains failed attempts, missing
messages, duplicates, reordering, per-run means and per-run tails.
