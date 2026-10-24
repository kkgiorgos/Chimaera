# Overnight orchestrator diagnosis, 24 October 2026

Experiment `52c15e35701e4bfbb5de0b3045db219e`, frozen revision
`8b1899f79f751cb90a4a5fc6b5466b9f4e4095e1`.
Controller snapshot: **2026-10-24 06:18:05 UTC / 09:18:05 Athens**.
Three completed cases are misses, three failed before launch, and two were still
running. This is a snapshot of an ongoing experiment, not its final outcome.
No runtime code, deployment, active case, or experiment setting was changed during
this investigation. No new simulation was started.

Subsequent fixes and deployment validation are recorded separately in
[Pop!_OS recovery](RECOVERY.md) and [perception recovery](PERCEPTION-RECOVERY.md).

## Evidence and reproduction

- [Snapshot and frozen configuration](snapshot.json).
- [Computed per-case evidence](summary.json).
- [Collected logs, records, RMW traces, configuration and statistics](evidence.tar.gz).
- [Recompute the summary](analyze.py):
  `python3 docs/measurements/orchestrator-overnight-2026-10-24/analyze.py`.

The archive contains the controller-collected six terminal attempts, including
failures. It excludes checkpoint memory, guest disks and uncollected live attempts.
The script verifies the archive hash and verifies each completed evaluation lasted
the configured 1.5 seconds after its actual launch. Failed-attempt perception
counts below exclude the saved telemetry prefix and use captures strictly newer
than the saved guest application time, 0.601613999471 seconds.

## 1. Failed cases: usable stereo readiness, not absent DDS traffic

| Case | Throw / pressure | Outcome | New post-checkpoint perception |
|---|---|---|---|
| 000 | loft-3 / baseline | Failed before launch | All 23 processed pairs expired |
| 001 | loft-3 / random 90% | Completed miss | See evaluation below |
| 002 | loft-3 / stride 90% | Completed miss | See evaluation below |
| 003 | loft-3 / stream 90% | Completed miss | See evaluation below |
| 004 | loft-5 / baseline | Failed before launch | All 23 processed pairs expired |
| 005 | loft-5 / random 90% | Running at snapshot | Not collected |
| 006 | loft-5 / stride 90% | Failed before launch | All 22 processed pairs expired |
| 007 | loft-5 / stream 90% | Running at snapshot | Not collected |

All three failed logs show physics replay of 1,192 intervals and clock alignment
of 12 intervals, followed by:

> Restored DDS did not reconnect fresh control and stereo traffic

That error comes from the bridge's **one simulated second** reconnection limit
([gate implementation](../../../ros/chimaera_ros_ws/src/chimaera_ros_bridge/src/gazebo_host_bridge.cpp#L163)).
It is not the 900-second startup timeout, the 12-hour case timeout, or an OOM kill.
The worker reports exit code 2 and `oom: false` for all three failures.

Fresh image RMW takes and new perception records prove images reached the guest
and callbacks ran. Effort commands also resumed. However, every processed capture
newer than the saved state was `expired_processing`. The host's readiness gate
explicitly rejects those records
([record handling](../../../ros/ball_catching_ws/src/ball_catching_sim/src/host.cpp#L154));
it also requires arm/perception readiness. Consequently, the error conflates a
missing connection with an application that cannot process its images while fresh.
The evidence establishes the latter in these attempts; it does not establish that
every transport property is correct.

Pressure starts only **after** this gate passes. Both unloaded baselines fail
here, and case 006's configured stride pressure had not started. Memory pressure
therefore cannot explain the common prelaunch failure.

## 2. Completed misses: no interception trajectory was published

All three completed cases have valid ballistic launch records, a full 1.5-second
evaluation, `outside_cup_at_deadline`, and zero containment duration. These are
scored misses rather than launch failures or truncated scoring windows.

| Case / loft-3 load | Recorded stereo pairs | Expired | Detections | State estimates | Interception searches | Published trajectories |
|---|---:|---:|---:|---:|---|---:|
| 001 / random 90% | 39 | 39 | 0 | 0 | None | 0 |
| 002 / stride 90% | 44 | 37 | 6 | 2 | One, stopped by search budget | 0 |
| 003 / stream 90% | 44 | 23 | 9 | 2 | One, no feasible intercept | 0 |

Counts use completed perception records captured within the evaluation window.
They exclude stale inputs discarded before a pair is processed and unfinished work
at shutdown. There is no per-input record for the former. Each trajectory count
is independently checked against the guest RMW publish trace.

Case 002's search ran at 0.947904707 s on the 0.8585 s capture: the state was
89.4 ms old. It used 455 IK iterations, found no inverse solution and stopped
after 27.44 ms. Although its nominal planner period is 33.33 ms, the search stops
at 80% of that period, approximately 26.67 ms, reserving time for handoff. Thus
`planning_deadline_missed` does not mean its elapsed time necessarily exceeded
33.33 ms.

Case 003's search ran at 1.642921125 s on the 1.5515 s capture: the state was
91.4 ms old. It used 425 IK iterations, found one inverse solution, and returned
no feasible interception after 26.32 ms. An inverse solution alone does not prove
a dynamically feasible approach and braking trajectory.

The planner rejects states older than 100 ms, and the filter needs five accepted
observations before publishing its first state. The sparse fresh pairs leave
very few planning opportunities. No case reached the stage where an interception
trajectory could be followed. Improving image latency is necessary to address
this observed failure chain, but these records do not prove that it alone will
produce a catch: search feasibility and the simulated-time budget also need a
successful baseline qualification.

All 375 expected effort publications in each completed evaluation have matched
host receipts. Received telemetry has zero sequence gaps. This supports continued
control/record transport progress; it does not measure all observer overhead or
prove the delivery of every generated record.

## 3. High image age: application service time and queued callback execution

The freshness budget is `2 / 30` seconds, **66.67 ms**. All numbers below are
**simulated milliseconds**, not host wall time.

| Case | Capture to paired callback, median | Detection segment, median | Capture to completed perception, median | Completion maximum |
|---|---:|---:|---:|---:|
| 001 / random | 48.71 | 34.48 | 84.60 | 100.52 |
| 002 / stride | 44.80 | 31.19 | 76.40 | 87.15 |
| 003 / stream | 37.08 | 31.03 | 68.07 | 90.42 |

The medians are separate distributions, not additive stage totals. Before launch,
the failed attempts' detection segment medians are approximately 38.4 ms, already
longer than the roughly 33 ms camera period, before other callback work.

The composed application puts perception and interception on the **same
SingleThreadedExecutor**
([layout](../../../ros/ball_catching_ws/src/ball_catching_robot/src/composed.cpp#L18)).
Its image callback performs stereo detection synchronously
([callback](../../../ros/ball_catching_ws/src/ball_catching_robot/src/perception.cpp#L125)).
For each image, that detection does HSV conversion, thresholding, morphology,
contour extraction and stereo matching. Application computation remains modeled
under functional copies. The camera subscriptions use SensorDataQoS with depth 5,
and matching a pair starts computation immediately; there is no independent fast
ingestion callback maintaining a latest-complete-pair mailbox for a worker.

The traces show the queueing directly. In case 001, the frame captured/published
at **2.4425 s** is taken on the right camera at **2.497750365 s**. The preceding
pair runs from **2.462300156 s** to its processing completion marker at
**2.496820793 s**, occupying 34.52 ms of this frame's 55.25 ms publication-to-take
wait. The frame is taken only 0.93 ms after that marker. Its own detection then
ends at 2.532085624 s,
approximately **89.6 ms after capture**, and is rejected. Across the completed
cases, median gaps from one perception completion marker to the next processing
start are only roughly 0.9–1.1 ms. Planning adds occasional approximately 26–27 ms
service intervals on this same executor.

This demonstrates a substantial callback-service/queueing contribution. The RMW
latency trace ends at **receiving take**, not device ingress, and the robot's
`receive_time` is later still, when both images are paired. Neither is a pure
transport-transfer latency. The archive has no independent device-ingress marker
that can partition every millisecond between transport and executor scheduling.
Detection segment time also includes guest scheduling/preemption; it is not an
isolated CPU-cycle profile of each OpenCV function. Trace/record observer overhead
has not been isolated in this run.

### Why the previous low-latency measurements did not predict this

The [earlier flow-isolation measurements](../chimaera-dds-flow-isolation-2026-10-23-round2/README.md)
observed functional-copy camera creation-to-callback maxima of **0.25–0.60 ms**
without bulk and **0.77–0.83 ms** with bulk. Those were short synthetic endpoint
tests: six camera samples per direction in each Timing source window, preallocated
images and lightweight receipt callbacks, without robot OpenCV detection or IK.
The [synthetic receiver](../../../ros/chimaera_ros_ws/src/chimaera_ros_bridge/benchmark/endpoint.cpp#L274)
records callback entry before inspecting the message. Its matrix used 250 Hz
joints and 2 ms coupling; this actual robot uses 1 kHz joints and 0.5 ms coupling.
The prior actual robot catches were KVM functional checks, not detailed Timing
latency validation. A low-latency synthetic receive path did not establish a
low-latency robot under its real computation and scheduling load.

Functional copying is active: the completed cases' final counters report about
92–95 MB copied and 45–47 MB filled through functional operations, with **zero
functional-copy faults**. This is not evidence of an accidentally disabled helper.
It removes scoped large middleware copy/fill execution, not OpenCV processing,
all middleware execution, OS scheduling, or application queueing.

## Deployment and wall-time findings

`pop-os` failed preparation while streaming the application image:
`Direct image transfer failed: Timeout, server ubuntu-george not responding.`
The last retained progress was about 1.67 GB sent. The record establishes an SSH
transfer timeout, not its underlying network/host cause. All eight cases were
therefore assigned to `ubuntu-george`.

The queued configuration expanded to **eight cases**, including 90% pressure,
with four host worker slots at 2 host CPUs and a 3 GiB soft RAM budget each. The
recommended first qualification was two unloaded cases, one worker, 4 host CPUs
and 12 GiB RAM. Actual sampled peaks were roughly 4.5–5.0 GiB per worker. The
worker host has six logical CPUs, so four workers' aggregate quotas exceed its
CPU count. These settings and memory use can lengthen host execution; no OOM kill
is recorded. A slow host by itself is not an explanation for tens of milliseconds
of simulated image age in lockstep co-simulation.

The dashboard's final per-attempt progress is also stale relative to its score:
case 001 still reports only 0.0805 seconds after throw in the worker progress
snapshot, although the durable result and motion records reach the full
1.5-second evaluation. Diagnose using result/records, not that final progress
field.

## Next changes and validation

1. Make readiness failures distinguish missing DDS traffic from stale input and
   expired perception; retain these counters/stages even on failed attempts.
2. Bound image backlog with independently serviced ingestion and latest complete
   stereo-pair selection. Keep shared filter/robot state synchronized and integrate
   any worker threads into checkpoint quiescence. Arbitrary executor concurrency
   would not be a safe replacement for the current serial state handling.
3. Profile/reduce detection cost on the selected simulated hardware and keep
   planner work from starving ingestion. Preserve the agreed modeling boundary;
   changing resolution, rates, CPU geometry or freshness limits is a separate
   experimental choice, and raising the age limit alone would retain older states.
4. Qualify repeated unloaded restores and an actual detailed-CPU catch with the
   one-worker allocation before interpreting memory-pressure effects. Add
   receive-before-executor instrumentation to separate transport from callback
   wait, with its observer cost reported.

Validation of this analysis: the reproducer completed successfully; archive hash,
full scoring duration, saved-prefix exclusion and RMW trajectory counts were
checked. The existing simulations were left running.
