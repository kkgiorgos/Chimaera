# Successful pop-os catch comparison and planner timing, 2026-10-19

The latest baseline miss is not explained by a weaker CPU/cache/memory setup.
An actual successful **gem5** catch on pop-os used the same hardware and physical
settings. The previous estimator correction is present in the failed guest, but
it is insufficient: a long failed planning callback prevents the node from using
newer observations during a short feasible-intercept window.

## Verified successful reference

The reference is
`pop-os:/home/kkgiorgos/University/Diploma/Chimaera/ros/ball_catching_ws/results/memory-orin-demo-warmup0/case-000-attempt-001`.
It recorded `success=true`, a verified catch, and 0.532 s final containment.
Its `gem5/cpu_roi.json` confirms four `BaseTimingSimpleCPU` cores in detailed
execution; its boot-time `config.ini` alone would misleadingly show the initial
KVM CPUs and `atomic_noncaching` memory mode.

The following match the newest failed baseline
`8db9733a598241f5a25ac7b4aeb44de8/case-000`:

- Four 3 GHz Timing cores; private 32 KiB, 8-way instruction/data L1s; shared
  8 MiB, 16-way L2; Ruby MESI two-level; 3 GiB RAM.
- Identical Ramulator LPDDR5 configuration SHA256
  `672af95b1aac3bbe2468fe60bb19a81982d41062499dc11b1cc1e0fe6aa657b1`.
- The 3 m/s throw from `[1.8, 0.04, 3]` along `[-1, 0, 2]`; camera at
  `[-2, 0, 2]`, 480×360 at 30 Hz; planner 30 Hz/60 ms plan interval;
  effort control 250 Hz, joint feedback 1000 Hz; physics/coupling step 0.5 ms;
  zero ready hold and a 1.5 s scoring window.
- The actual robot URDF, including geometry, inertias and joint limits. A
  normalized XML comparison has no differences after removing mesh-file path
  prefixes. The kernel SHA256 also matches.

The complete executions are **not identical**. The successful reference records
revision `4cb3a2a`, gem5 SHA256 `57043274b45bbdf74a1aca8207eeb65bb1b3289dc86e0480f562e6d6dc7e7a03`
and bundle SHA256 `7c0bd3453b5fcc02c7c4b4e93f26b742a17cfbb66106d4c971a32414555de968`.
The newest run uses the corrected estimator at `b6eefaa`, different frozen
simulator/application/guest provenance and readiness-checkpoint restoration.
Its application settings add the checkpoint gate and detailed timing socket.
The historical run boots with KVM and switches to detailed execution. Their
launch times and camera/launch phases differ.

Other inspected successful gem5 references include 100 ms ready holds, and one
uses a 2 s scoring window. They must not be described as identical configurations.
The preserved successful 5/7 m/s host trials are **native**, with 90 Hz cameras;
they do not qualify the current 30 Hz guest configurations.

## Recorded timing failure

The newest 3 m/s baseline launches at 0.5045 s. Its first planner callback starts
at 0.705 s and completes at 0.802956001 s, with no feasible plan. It performs
1,680 IK iterations: fourteen seed attempts each exhaust the 120-iteration limit.
Reachable candidates in the useful observation window converge in 4–11 steps.

New observations completed in perception at approximately 0.7310, 0.7636 and
0.7955 s, while the single-threaded planner was processing the earlier failed
search. Replay finds feasible trajectories with these states when used early
enough and with unchanged motion limits. Perception completion does not prove
DDS delivery at that instant. The regression explicitly checks that the state
completed at 0.7310 s works at the next 0.738 s timer tick, but is already too
stale for the future-start trajectory at 0.771 s. Its next recorded search, at 0.837 s, uses a
different observation and rejects every trajectory. It never commands a catch.

The successful pop-os reference produced a feasible first plan at 191.5 ms after
launch, despite that search costing 119.9 ms. The newest failure starts its first
search at 200.5 ms but gets an unfavorable initial estimate. Long search latency
was already present in the successful build; the different sampling/startup
state makes that existing weakness fatal in the newer run.

The failed guest's recorded filter states exactly match an independent C++ replay
of the corrected filter. This rules out simply reusing the old estimator for this
particular failure. Earlier instantaneous replay/native checks did not establish
that the guest could process the useful observations before their deadlines.

## Timing correction

The ROS node and regression tests now share the pure `CupInterceptPlanner` search.
It retains the core IK solver's 35-iteration bound per seed, tries lower catch
heights with longer flight time first, and supports interrupting the search while
retaining its best feasible result. Joint speed, acceleration, tilt, braking and
physical/scoring limits are unchanged.

The node budgets 80% of one planner period for search, with checks between bounded
IK attempts and trajectory candidates. The remaining time covers finishing the
current check and command handoff. It schedules the new trajectory one planner
period ahead and rejects results that have already missed that start time.
The effort controller and planner retain the preceding trajectory until the new
timestamp, preserving position/velocity/acceleration continuity rather than
jumping ahead as soon as a future replacement is received. The 60 ms update
interval now applies to successfully published commands: an unsuccessful search
leaves the next 30 Hz timer tick free to use a fresher observation instead of
consuming another useful tick.

Records now include IK calls/iterations, planning budget and trajectory start
time. Reports distinguish `planning_deadline_missed` from geometric/dynamic
infeasibility. A geometric rejection is no longer used to label an interrupted
search that has not checked every alternative.

## Validation and limits

- All **27 C++ checks** pass in ROS Humble and Jazzy. The timing regression uses
  visual states and independently logged arm positions from the actual failed
  run. It checks the work bound, availability of the next useful observation,
  valid future trajectories, retention of a feasible partial result when
  interrupted, and preservation of active/future command intervals.
- **130 Python workflow checks** pass in the Humble container. Two dashboard
  checks skipped there because Node is unavailable both pass on the host.
  JavaScript syntax and patch whitespace checks pass.
- The final deadline-aware code catches **3/3 native** repetitions of the
  unchanged 3 m/s reference configuration, each retaining the ball for 0.5315 s.
  These final trials use successful-command-only update throttling; native
  success is still insufficient to establish guest observation-delivery timing.
- Standalone gem5 SE kernel probes use a single measured 3 GHz Timing core,
  the same L1/L2 sizes/associativities and LPDDR5 preset. Initialization runs on
  an Atomic core before switching to Timing at the measured workbegin marker.
  They exclude Linux scheduling, ROS, coupling, other cores and physics.
  A fixed 35-iteration search takes 26.301 ms on the failed input and 39.507 ms
  on the useful input. Thus an iteration reduction alone still exceeds a 30 Hz
  period on a successful search. The final deadline-aware useful-input probe
  returns a **feasible interrupted search in 28.259 ms**, including the final
  bounded check after its 26.667 ms search deadline, before the 33.333 ms
  trajectory start. It uses 253 IK iterations in eight calls.

These kernel/native results are not an end-to-end coupled catch validation.
No full new gem5 throw was started, and no live experiment, image or checkpoint
was changed. A fresh application build, guest bundle and readiness checkpoint
must contain the correction before a single unloaded coupled 3 m/s baseline can
qualify it. Do not resume a frozen old cohort with these new binaries, and do not
treat a successful native or planner-only probe as qualification of the full
interference/faster-throw matrix.

## Preserved records

`results/ball-catching-planner-debug-20261019/` contains ignored diagnostic
artifacts: successful pop-os configurations/URDF and newest failed records under
`reference/`; regression fixture; exact native validation scripts, logs and raw
trials; fixed-iteration and deadline-aware C++ probe sources/binaries; gem5 SE
configurations, logs, statistics and `timing.json` files; source/binary hashes in
`validation-summary.json`. Every kernel probe had a 180 s wall limit. The
120-iteration control exceeded that limit and has no completed kernel duration;
the observed full-system callback timing is preserved separately.

## Test through the orchestrator

Import `orchestrator/experiments/planner_timing_baseline.json` into **New
experiment**. This uses a repository-root-relative Ramulator path, as required by
the dashboard. **Validate** must show exactly one case: `loft-3`, `camera_30`,
zero pressure. The configuration preserves the failed baseline settings,
including the default 50 ms checkpoint pressure hold, made explicit here.

Before queueing, put the timing correction on the controller and every selected
worker at the same Git revision and source contents. Committing and distributing
the changes is the simplest way to ensure this; uncommitted and untracked sources
also contribute to the frozen fingerprint. Rebuild the controller deployment
from `orchestrator/` with `docker compose --env-file .env up -d --build`, and
redeploy the selected supervisor through the dashboard.

Select one preparation machine and one worker slot for this first test. Name the
new experiment `planner-timing-baseline` and queue it when the existing experiment
has finished or has been stopped intentionally. Existing frozen runs retain
their old application and checkpoint; retry/resume cannot adopt this correction.
No cache deletion is required. Application source hashing includes the new search
file, and a new experiment prepares a checkpoint containing that application.
The SDK can still reuse matching inputs.

Inspect `interception.jsonl` for `trajectory_start_time`, `planning_budget_seconds`
and `inverse_iterations` to confirm the new planner is running. A useful
interrupted search can still have `status=planned`; an empty interrupted or late
search is `planning_deadline_missed`. The qualifying outcome is a recorded catch
in `result.json`, not merely a plan or a completed preparation. Keep the full
3/5/7 m/s pressure matrix for after this unloaded coupled baseline succeeds.
