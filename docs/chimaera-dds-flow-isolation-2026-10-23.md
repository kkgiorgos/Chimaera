# DDS m5ops flow isolation investigation, 2026-10-23

## Requirements

The target is sustainable delivered throughput while keeping simulation-time
control age close to a repeatable unloaded baseline. No numerical regression
allowance is selected yet. Scheduled release, actual publication, publish return,
and callback entry are distinct events; missing source releases remain failures
even if every actually published sample is received.

The user's primary latency is **actual sample creation/capture to callback entry,
in simulation time**. Retain guest-copy and functional-copy modes and report them
separately, conditional on an accurate end-to-end model. The first-round benchmark
preallocates and reuses payloads without timestamping per-sample creation.
Its `latency` is publish-call-entry to callback, while `age` is scheduled-release
to callback. The second revision separately timestamps logical preparation start,
while physical capture and initial payload allocation remain outside that boundary.
The report exposes these distinctions alongside source release coverage. Actual
robot validation must place a timestamp at source capture/creation, before
per-sample preparation, and retain publication and callback timestamps separately.

Functional-copy mode currently gives scoped memory copies zero ordinary modeled
instruction/memory cost; it is an explicit alternative model. Its end-to-end
timestamp arithmetic can be correct while its assumed operation costs are too
optimistic for a particular target. An intended offloaded implementation needs
its execution/transfer cost represented or calibrated before this mode supports
a physical-system latency claim.

The accepted per-topic target policy is latest-sample delivery for stereo,
joint feedback, effort commands, clock, and complete replacement trajectories;
benchmark records are lossless. Stereo replacement uses complete capture-matched
pairs rather than arbitrary independently selected eyes. Replacing a trajectory
assumes a complete replacement message, not incremental motion commands.
At recorder overload, the user explicitly permits record backpressure to delay
its producer, including a control callback. Those waits are measured separately
and are an explicit exception to the latency target. Bulk-load comparisons use
a draining recorder; a stalled record flow must not hold shared locks/credits
needed by unrelated producers. This is the target contract; the present bridge
still lacks the full per-flow policy/credit implementation.

The existing two-core CPU/cache/memory geometry stays fixed. DDS/RMW, bridge, and
application changes are allowed. Independent topics/flows need configurable
priority, bounded per-flow accounting, reserved control progress, and a per-stream
choice of lossless or latest-sample delivery. Begin with robot workload behavior,
then use synthetic traffic to find remaining capacity. Measure guest-executed and
host-offloaded middleware separately before choosing the modeling boundary.

KVM supplies fast diagnostics and correctness checks. The first timing-model round
has a total **7,200-second host wall-time budget**, including runner preparation.
Use a 7,140-second runner deadline, reserving one minute to stop its own container
and retain logs within that cap. It must not start another case after its deadline.
Unstarted and interrupted cases remain visible. No simulation-time latency
threshold is inferred from KVM results.

## First controlled diagnostic

The benchmark endpoint has two explicitly selectable application layouts:

- `isolate_bulk_execution: false` preserves one executor and the merged source
  schedule.
- `isolate_bulk_execution: true` assigns stereo/image/bulk source publication and
  typed subscriptions to one additional executor thread. Controls, clock, source
  management, and event export use the original executor. Both retain one DDS
  participant and the same transport pools and receiver threads. This tests
  application execution coupling; it does not implement per-flow bridge isolation.

`trace_publish: true` records return from each publish call. The additional
`publish_return_ns` CSV column is optional for readers of historical evidence.
The subsequent `trace_creation: true` revision records `creation_ns` before
each logical sample's per-sample preparation. It retains preallocated payloads;
initial allocation and physical capture are outside that benchmark timestamp.
Analysis reports `creation_to_callback` and `preparation` separately and never
invents creation timestamps for historical rows. This revision is compiled and
unit-checked, but has not run in KVM or timing; all retained measurements predate
it. Freeze matching new host/guest endpoints before testing the extended record
format. It does not change the already frozen comparisons.
Source/callback records are buffered and exported after drain. Shared recording
and lifecycle state is synchronized; source stream state belongs to exactly one
executor. Export waits for an in-progress bulk callback/publication to finish.
Timing instrumentation is identical in the compared layouts. It still adds work,
so an eventual candidate needs an uninstrumented comparison too.
The host logical clock advances at completed coupling boundaries, so a host
publish call can have zero recorded simulation-time duration despite doing host
work. Guest publish-call timing is the useful additional stage measurement here.
The first split diagnostic also installs a second 1 kHz source timer even when
the guest only receives bulk data. Its extra wakeups are part of the measured
guest cost. A production scheduler should suppress idle bulk-source wakeups and
schedule sparse sources from their next release; the first diagnostic is not
presented as the final resource-minimal executor implementation.

The diagnostic keeps the existing ball-shaped 480x360 stereo at 30 Hz, 250 Hz
joints/effort, and 1 kHz clock. These are representative synthetic endpoints,
not an actual robot catch. The first KVM matrix verifies all image bytes, with
one-second source windows. The first timing matrix interleaves three unloaded
repetitions per layout, then adds the existing transient bidirectional 8 MiB
case per layout. Its 0.2-second source windows permit descriptive repeat
variability and stall attribution, not a sustainable bandwidth or tail guarantee.

New selections, bundles, simulator bytes, source snapshots, and output directories
are retained under `.chimaera-m5/isolation-20261023/`. Frozen tooling lives in
`.chimaera-m5/tooling-isolation-20261023/`. The previously selected kernel moved
to `assets/kernel-14ac.../kernel`; its SHA-256 is unchanged from the prior
`kernel-7b7d.../kernel` selection, as are the notification module and symbol table.
Existing measurements and guest images are not modified.

## Production decisions that remain to be implemented and measured

A separately frozen [functional-copy experiment](../profiling/chimaera_m5/copy_offload/README.md)
implements a narrower offload boundary for measurement: large copies/fills inside
RMW publication/take and custom DDS receive scopes execute on the host. Application
work stays in the guest. Both comparison arms preload the same helper, with mode
disabled/enabled. A new capability bit and optional data commands preserve the
existing notification ABI. Guest prefault stores and calls remain modeled. Native
memory-contract checks and the rebuilt DDS checks pass; the simulator extension
builds. All four separate KVM cases (unloaded/8 MiB, guest/functional mode) pass
full-payload verification. Operation counters are zero in guest mode, positive
in functional mode, and report no memory faults. Timing results are retained
separately in the [measurement report](measurements/chimaera-dds-flow-isolation-2026-10-23/README.md).

Flow isolation must span participant submission, host router/controller admission,
device queues/credits, guest receive dispatch, and application execution. A
priority queue at one stage cannot reserve buffers or preempt an uninterrupted
large copy/typed take at another stage.

Prefer bounded shared workers and explicitly provisioned flow budgets over a DDS
participant, large receive pool, and thread for every topic. Preserve packet order
within a flow and explicit dependency group. Scheduling can reorder independent
flows; stereo/causal dependencies need explicit treatment. Bound work in bytes and
records, and keep a minimum bulk service share to prevent starvation.

Latest-sample policy belongs at a complete application-sample ownership boundary.
Replacing arbitrary queued RTPS packets is unsafe: one sample may be fragmented,
and packets can carry reliability/discovery metadata. DDS QoS/history and endpoint
policy must agree with any bridge replacement rule. Lossless pressure must not
hold a global DDS lock while waiting for a slow flow's credits. A transport
callback rejection is not proof of publisher backpressure: the pinned Fast DDS
version ignores that callback's return value.

The existing Fast DDS participant send-resource lock covers transport callbacks.
Its resource list owns unique pointers; simply releasing that lock before a send
would introduce lifetime/concurrency hazards. Any narrower locking patch needs
safe ownership through concurrent endpoint disposal and tests of those paths.

Reserved flow credits and total pool bounds must be compatible with the supported
number and size of concurrent flows. Fair scheduling alone cannot guarantee an
idle flow a receive slot if another flow retains every shared slot. Conversely,
allocating a maximum-sized buffer per topic would undermine the guest-memory goal.

For the offloaded variant, report exactly which instructions, serialization,
allocation, DDS work, and memory accesses move outside modeled guest execution.
Compare typed message contents, ordering, QoS and admission behavior. Faster
simulation-time delivery obtained by changing that boundary must remain a separate
model result, not be attributed entirely to a bridge implementation improvement.

## Validation required before claiming success

An actual robot application must remain functional, followed by source/delivery
correctness checks under mixed per-stream policies. Stress independent flows,
both directions, multiple bulk publishers, bursts, a slow/stalled consumer, and
endpoint teardown. Measure fixed receipt-window goodput, release coverage,
scheduled-to-callback age, per-flow queue/credit occupancy, backlog trend,
instruction/memory counters, resident memory, threads, and notification work.
Receipt during drain divided by source duration is finite-cohort accounting and
cannot establish maximum sustainable throughput.

## First-round findings

The repeated unloaded execution layouts establish observed variability before
setting a numerical regression allowance. With merged execution, unloaded
joint/effort/clock per-window p99 spans 3.29–5.98 / 2–4 / 5.29–6.56 ms.
With split execution it spans 1.18–4.36 / 2–4 / 2.07–4.40 ms. Three short
repetitions do not establish a stable tail distribution: each source window
contains only 50 joint/effort and 200 clock samples.

The merged 8 MiB case has joint/effort/clock p99 of 113.10 / 82 / 117.12 ms
and twelve missing scheduled effort releases. Split execution records
2.43 / 4 / 3.78 ms and no missing effort releases. Every transmitted control
arrives by drain in both layouts. The one missing scheduled clock release is
also present in unloaded cases. Cameras still reach 102–104 ms with split
execution, so this result demonstrates control protection for the tested
transient, while camera/bulk isolation remains unresolved.

In the separate, matched memory-cost experiment, the loaded guest-copy arm
records 63.64 / 67.26 ms camera maxima and 59.04 ms guest bulk publication.
Functional mode records 1.58 / 1.64 ms camera maxima and 16.38 ms bulk
publication. Its counters report 100,476,320 copied bytes, 51,274,313 filled
bytes and zero faults in the retained final dump. The respective final raw
instruction counts are 103,211,578 and 89,084,094 over 0.354 s, including
warmup/drain/export work. These are model-specific results, with a fresh
simulator/library/helper in both arms; cross-phase changes are not attributed
to offload. Ordinary guest work remains the default; both modes are retained
and reported separately, with their different modeled costs explicit.

Actual robot scene generation and kinematic checks pass in the complete app
image. The fixed two-core KVM catch attempt exits with `m5_fail` code 2 before
bridge readiness, and provides no scored catch. Its cause remains unresolved;
the historical four-core success does not qualify this configuration. Recovery
of that application baseline precedes claims about the actual robot workload.

The measurement export includes complete and interrupted cases, source and
delivery coverage, per-stream ages, optional publish brackets, raw statistics,
and artifact identities. The first round cannot establish maximum sustainable
throughput, full per-flow independence, or a numerical latency guarantee.

## Second bounded round

The user authorized another two hours: 17:37–19:37 UTC, including preparation,
with one minute reserved for cleanup. Evidence is separate under
`.chimaera-m5/isolation-20261023-r2/` and in the
[second-round report](measurements/chimaera-dds-flow-isolation-2026-10-23-round2/README.md).

Failure-only UART draining exposed the original startup error: the retained
robot guest runner rejects the newer `trace`, `trace_directory`, and
`trace_clock_file` manifest fields. The recovery adapter explicitly disables
tracing and removes only those unsupported fields. Transport settings and
robot binaries remain intact. The retained three-process robot then boots on
two cores but does not reach arm readiness inside its 600-second attempt.

An opt-in `composed:=true` robot layout shares one ROS context and DDS
participant across perception, interception and effort control. Effort control
uses its own executor; perception/interception share a second executor. This
reduces duplicated process/context resources without changing physics, rates,
gains or algorithm source. Default launch behavior retains the separate nodes.
The composed candidate passes the actual two-core KVM catch: launch at 0.206 s,
evaluation at 1.706 s, and 0.5315 s contained in the cup. Its ballistic launch
validation and CPU record pass. A postprocessing import failure in the old host
Python runtime is recovered offline from the saved score, telemetry and stats;
the wrapper's original nonzero return remains recorded.

The first effort receipt is 0.0805 s for the composed candidate versus 0.5405 s
for the unsuccessful retained layout. These single KVM observations are startup
diagnostics, not TimingSimpleCPU latency comparisons.
The candidate also includes current feedback diagnostic logging absent from
the retained executable. The startup observations are not a fully matched
causal estimate of composition alone. Core/runtime CTest checks,
five launch checks, and native startup/SIGINT lifecycle checks pass. The native
smoke reports 13 threads and about 31 MiB RSS without sensors; it does not
establish guest memory or thread counts under robot load.

Matching creation-instrumented endpoints are newly frozen in both host and guest.
Four KVM cases verify complete payloads, creation/preparation coverage, and copy
mode counters before timing. The timing order prioritizes two interleaved
unloaded references per mode, the loaded pair, then third references within
the same absolute deadline. All eight planned cases complete, including three
unloaded windows per copy mode. The robot's 0.5 ms coupling/physics and 1 kHz joint feedback differ
from the synthetic diagnostic's 2 ms coupling and 250 Hz feedback; they are
not pooled as interchangeable workload measurements.

The functional-copy simulator used by the synthetic suite lacks the optional
Ramulator backend required by the actual robot. That actual-copy attempt fails
before boot and supplies no model result. A separate rebuild with the pinned
Ramulator dependency instantiates the complete robot at tick zero; it remains
preparation for a future actual functional-copy robot run. The active suites
retain their original frozen simulator bytes.

With creation instrumentation, ordinary-copy unloaded joint/effort/clock
per-window p99 spans 0.367–2.832 / 1.695–3.662 / 0.546–3.621 ms.
The loaded case records 4.010 / 2.127 / 3.167 ms: joint p99 is 1.178 ms
above the highest unloaded value, while effort and clock do not exceed their
observed unloaded maxima. Functional-copy unloaded values span
0.291–0.722 / 1.304–1.990 / 0.374–0.746 ms; loaded values are
0.420 / 1.282 / 0.532 ms. No loaded control p99 exceeds its highest
functional-copy unloaded reference. Both loaded cases deliver all published
controls by drain, with no additional missed releases beyond the one clock
release also absent unloaded.

Loaded ordinary-copy camera creation-to-callback maxima are 84.472 / 86.561 ms;
functional-copy maxima are 0.768 / 0.835 ms. Guest bulk publication peaks at
52.709 ms and 15.511 ms respectively. These measurements begin before logical
sample preparation with reused payloads, not physical capture or initial
allocation. Each loaded case has only one measured 8 MiB sample per direction.
Warmup samples can arrive in the receipt window; the export distinguishes those
from the measured publication cohort. Sustained throughput, independence across
other burst phases, and physical offload timing remain unqualified.

A separate short TimingSimpleCPU/Ruby correctness case scans every byte of one
complete image per eye with functional copies enabled. Both arrive with no
missing, duplicate or reordered samples, and functional-copy faults remain zero.
This checks cache-visible correctness; its payload scan is excluded from the
latency comparison. Burst-phase sensitivity and an actual functional-copy robot
run are not started within the remaining budget.

The default standalone perception, intercept and effort executables still build.
All 84 profiling-tool unit tests pass. All owned simulations are stopped and
checked after 6958.5 seconds of the 7200-second second-round budget. Per-topic
priority, reserved credits, mixed-policy admission and sustained stress testing
remain outstanding.
