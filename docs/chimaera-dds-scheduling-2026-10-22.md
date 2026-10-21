# DMA/DDS scheduling changes, 2026-10-22

The DDS guest coordinator now consumes DMA completions directly and starts
delivery of admitted packets on the calling thread. Small packets with writable
local sockets can reach the participant without scheduling a completion worker,
ROS executor or router worker first. Partial socket writes retain the existing
worker fallback, FIFO offset and DMA loan.

Across three matched performance repetitions, stereo-pair mean delay falls
from **22.039 to 19.758 ms (10.3%)**. Observed stereo tails regress: p95 is
higher in every repetition and the maximum rises from **76.924 to 80.447 ms**.
This is an average-latency improvement with a measured tail tradeoff.

The baseline is the already copy-reduced implementation at `c3393c8`, rather
than the earlier eight-copy implementation. These changes target dispatch and
wakeup overhead identified by the
[image pipeline profile](chimaera-dds-image-pipeline-profile-2026-10-21.md).

## Performance results

All three performance cases per version deliver every measured message, without
duplicates or reordering. Across the repetitions, each version publishes and
receives 90 images per camera, 750 joint messages, 749 effort commands and 3,000
clock messages. Means are weighted by receipt count.

| Stream | Baseline mean, ms | Candidate mean, ms | Change |
| --- | ---: | ---: | ---: |
| Left image | 16.970 | 16.410 | -3.3% |
| Right image / complete stereo pair | 22.039 | 19.758 | -10.3% |
| Joint feedback | 19.756 | 18.302 | -7.4% |
| Effort command | 8.191 | 5.789 | -29.3% |
| Clock | 31.055 | 27.717 | -10.7% |

The pair mean improves in two matched repetitions; the second is slightly
slower. Per-run stereo p95 rises in all three. The observations do not support
a claim of better image-tail latency. Effort-command maximum falls from
56.094 to 45.167 ms; joint, clock and image maxima increase.

| Repetition | Baseline mean, ms | Candidate mean, ms | Baseline p95, ms | Candidate p95, ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 25.252 | 19.502 | 53.826 | 54.347 |
| 2 | 18.417 | 18.728 | 47.870 | 61.475 |
| 3 | 22.446 | 21.044 | 47.661 | 51.818 |

These are independent distributions. Per-run p95 values are retained rather
than averaged into a pooled percentile. Mean case wall time is essentially
unchanged, 113.005/112.860 s for baseline/candidate. Coupled wall-time settling
remains separate from the simulation-time callback delays above.

## Image-byte verification

Both verification cases receive all 30 images per camera and check every pixel
successfully, with no image duplication or reordering. The candidate also
receives all 250 joint messages, 248 published effort commands and 1,000 clock
messages. The baseline receives its 250 joints and 249 published efforts, but
only **968 of 1,000 clocks**, leaving 32 missing at drain. That shortfall is
retained; its cause was not isolated.

Pair mean is 20.090/22.905 ms for baseline/candidate in this single, separately
loaded pair. This does not establish a latency benefit with the verifier active.
The incomplete baseline clock stream also prevents a lossless whole-workload
comparison. Verification-cohort percentage comparisons are therefore withheld.

The final eight-case matrix receives all **480 measured images**, including
120 with full byte verification. All six performance cases and the candidate's
verification case deliver every published stream message. The separate initial
six-case experiment adds 360 received images; its source and results remain
identified as the direct-wait-only phase.

## Implementation

`DmaGuestController` supports `DmaCompletionMode::caller`. Its `run_next()` reads
the nonblocking driver completion queue on the calling thread. `completion_fd()`
exposes a borrowed descriptor for readiness only; the controller remains its
sole reader. The default worker mode keeps the existing notifier interface.

Guests selecting both DMA and Fast DDS use caller mode. Their coordinator has
no application ROS subscriptions, so it skips the callback pump and waits on the
driver plus a persistent eventfd. Router notifications, loan-release failures
and ROS shutdown signal that eventfd. Notifications before a wait remain
pending and bursts can coalesce. Legacy ROS bridge sessions retain their
completion worker and executor.

After `Router::receive()` validates and admits a cross-simulator packet, it
attempts nonblocking writes immediately, with a budget of one maximum CDDS frame
per recipient. It drains each recipient's existing FIFO first. Full acceptance
releases the queued owner; partial writes retain both owner and byte offset for
the worker. The byte limit still charges a whole packet per recipient until it
is fully accepted. Failed writes mark the client for retirement by the worker,
which owns its outstanding poll snapshot and closes the descriptor safely.

The router change applies on both sides after their respective causal admission.
Host-to-guest input still enters only at a paused simulation boundary. DMA and
CDDS framing, RTPS packet order, endpoint registration, queue bounds and local
fanout retain their existing contracts.

## Validation method

Both versions are Release builds in the same ROS Humble SDK on `ubuntu-george`.
The endpoint, clock plugin, patched RMW, DMA module, simulator and DDS profile
match. The candidate rebuilds the guest controller/bridge and DDS router. Each
case boots a fresh guest from the same read-only disk and matching kernel/module.

The performance matrix alternates baseline/candidate, candidate/baseline,
baseline/candidate. Configuration:

- Two `X86KvmCPU` cores, default 1 ms KVM quantum and `atomic_noncaching` memory.
- 480 × 360 RGB stereo at 30 Hz per camera, 250 Hz joints and effort commands,
  and a 1 kHz clock. Reliable depth-128 endpoints and lossless routing.
- SHM-only local DDS, 16 MiB segments, 1 MiB RTPS limit, 8 MiB DMA slots with
  16 RX/four TX slots, 2 ms coupling and 1 ms physics.
- 0.25 s warmup, 1 s source window and 0.75 s drain: 30 measured stereo pairs
  per completed case. Actual source counts remain explicit for every stream.

The performance repetitions disable the synthetic image verifier. A separate
baseline/candidate pair verifies every image byte after the callback timestamp;
that work can delay subsequent callbacks. It is a correctness cohort with one
run per version, separate from the three-repeat performance comparison.

An initial six-case experiment changed only DMA waiting. Stereo-pair mean was
16.355 ms for the baseline and 16.448 ms for caller mode, with complete delivery
of all 360 measured images and all other published messages. This is effectively
neutral and does not establish an end-to-end gain from removing the completion
worker alone. Its [comparison](measurements/chimaera-dds-scheduling-2026-10-22/direct-wait-only.json)
and frozen source/raw records are retained separately. The final matrix includes
both direct waiting and immediate router egress; separate fresh-boot phases
must not be subtracted to assign an exact benefit to either individual change.

## Regression checks

All 18 selected SDK tests pass: nine transport tests, seven bridge/wakeup tests
and both DDS router/integration tests. The DMA ownership/progress scenario runs
in both completion modes, checking caller-thread reads, registered TX staging,
RX loan retention, FIFO/coalescing, bounded queues, malformed startup, short
completion records and persistent failure. A worker-mode controller rejects
access to its completion descriptor to prevent competing readers.

The wait test covers notifications before and during a blocked wait, coalesced
bursts, direct driver readiness, simultaneous readiness, preservation of driver
records and closed-descriptor failure. The router regression pauses its worker
after poll readiness, proving that two admitted packets arrive immediately and
in order. It checks deferred retirement and owner release after a failed fast
write. Existing regressions retain split-frame, slow-recipient, fanout, loan,
capacity, malformed-ingress and boundary-admission coverage.

The new immediate-delivery regression fails against the baseline library with
`admitted packets waited for router worker`, as expected. The test intercepts
both `poll` and the Release SDK's fortified `__poll_chk`; production hardening
is retained. Final build/check logs and the expected negative control are in
the raw evidence.

KVM measures the complete synchronization/transport/DDS/callback path. It does
not model detailed CPU or memory costs, and these runs add no scheduler tracing.
The observed end-to-end difference cannot be treated as the CPU time of the
removed handoffs. Short independent windows also cannot establish stable tails
or a real-time guarantee. Full robot catching and detailed CPU qualification
require their own validation.

## Evidence and reproduction

The [interactive report](measurements/chimaera-dds-scheduling-2026-10-22/report.html)
selects streams, cohorts and per-run mean/p95/maximum. The
[comparison JSON](measurements/chimaera-dds-scheduling-2026-10-22/comparison.json),
[CSV](measurements/chimaera-dds-scheduling-2026-10-22/comparison.csv) and
[provenance](measurements/chimaera-dds-scheduling-2026-10-22/provenance.json)
retain counts, configurations, per-run tails and source/artifact hashes.
[Raw evidence](measurements/chimaera-dds-scheduling-2026-10-22/raw-evidence.tar.gz)
contains both phases, CPU origins, commands, event records, deployed gem5
configuration and final test/build logs. Repeated base64 guest readfiles and
redundant ROS/Gazebo log directories are omitted. Frozen baseline/candidate
source archives and the initial candidate source are retained.

Build both runtimes with the pinned RMW overlay sourced. The retained
[experiment tools](measurements/chimaera-dds-scheduling-2026-10-22/experiment-tools.tar.gz)
include the exact build/staging, execution and collection scripts. They reuse
the existing KVM pair runner, prefix each runtime's library directory and keep
all attempts. The baseline source archive is separate from the candidate
snapshot named in the generic per-case provenance.

```sh
python3 profiling/chimaera_rewrite/dds_large_messages.py \
  profiling/chimaera_rewrite/run_copy_cases.py CASES.json RUNS \
  --runtime CANDIDATE_RUNTIME --bundle CANDIDATE_BUNDLE \
  --original-runtime BASELINE_RUNTIME --original-bundle BASELINE_BUNDLE \
  --gem5-root GEM5_ROOT --image READ_ONLY_IMAGE --kernel MATCHING_KERNEL \
  --source-snapshot CANDIDATE_SOURCE.tar.gz
```

Use `cases.json` for the performance repetitions and append `verified-cases.json`
with `--append`. Keep the two cohorts separate. The exporter checks matching
CPU geometry, endpoint/RMW, simulator, DMA module and DDS profile, and keeps the
verification baseline's missing clocks visible. Initial test-interception and
staging retries are retained in the build/check logs; final checks pass.
