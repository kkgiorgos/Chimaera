# Perception recovery on ubuntu-george

Follow-up to the [overnight diagnosis](README.md) and [Pop!_OS recovery](RECOVERY.md).

The failed ubuntu-george cases (`case-000`, `case-004`, `case-006`) received
stereo traffic and resumed arm control. They failed before throwing because
every newly processed stereo pair expired before detection finished. The old
"Restored DDS did not reconnect" message obscured this application readiness
failure. Aggression was not enabled yet, including for the stride case.

## Changes

Camera subscriptions retain one sample per side instead of five. Ball-state,
geometry and planner feedback subscriptions also retain the latest sample.
Perception measures its processing cost on the image clock and skips queued
pairs without enough remaining deadline budget. Its estimate decays after
outliers and when otherwise fresh pairs are skipped, so a pessimistic estimate
cannot permanently block processing at a nonzero transport latency. The existing
post-detection and post-estimation freshness checks remain enforced.

Only completed fresh work refreshes perception readiness. Readiness publication
now runs at half the image-age budget, capped at 100 ms, so a fresh result can
be reported before it expires. These changes preserve the serial executor and
checkpoint freeze contract; they introduce no additional guest threads.

The host writes `checkpoint-reconnect-status.json` while waiting for readiness.
Failure reports distinguish live control, arm readiness, perception readiness,
fresh processed stereo, and the number of received/expired stereo records.
Missing diagnostics retain the original error message.

The camera dimensions/rate, 66.67 ms image-age budget, simulated CPU/cache/memory,
coupling interval, physics, arm controller, and planner search remain unchanged.
The changes address stale work and misleading failure reporting. They do not
establish that the planner can complete a catch on this simulated hardware.

## Validation and deployment

Native ROS tests cover the actual perception node with an intentionally stalled
executor and four queued stereo pairs, completed-work readiness expiration,
and native versus gem5 clock origins. Admission tests cover deadline headroom,
invalid timestamps, and recovery after an outlier even without successful
intervening work. All **33 C++ test cases** passed. Existing dynamics, estimator,
runtime, host physics and checkpoint replay checks passed. Python workflow,
container and orchestrator report/checkpoint checks: **188 passed, 2 skipped**.

A production-logging readiness capture completed in 193.46 wall seconds using
the original managed guest disk/kernel and requested Timing CPUs. A separate
restore without `/dev/kvm` checks fresh traffic, reader acknowledgements,
checkpoint release, contiguous telemetry sequences and measurement start.
It passed: reconnection took 79.5 simulated ms; the first new stereo pair
arrived 2.50 ms after capture and completed at age 35.38 ms, including 32.87 ms
of processing. The throw was released and its initial ballistics validated.
The deliberately shortened 2 ms smoke duration is not a catch qualification.
The first capture/restore used application `sha256:5267264dee54e09040790229212cbc6a10edcc6694e0eb8cba9647202447f778`.
The final production image includes the subsequent native image-clock correction
and starvation regression:
`sha256:d5a0abdd6279fc631280ad266f91742be0fecf6c6f3f438b86f012670559e0fc`.
Its new checkpoint index is
`7bd7fc88e531338f61ef6ce2e6e152c4a722d0125db55d7784e43898f1847052`.

Production `case-000` on ubuntu-george restored without KVM and passed the full
startup gate. Reconnection took 74.5 simulated ms (323.01 wall seconds). Its
first new pair arrived after 2.86 ms and completed at age **36.80 ms**, including
33.94 ms processing. Record sequences continued without gaps, the checkpoint
was released, measurement began, and ballistic launch validation passed.
This directly exercises the baseline that previously failed before throwing.
The detailed 1.5-second evaluation is running; its catch outcome is pending.

Production `case-001` on Pop!_OS also restored without KVM, passed the same
startup gate and validated its throw. Reconnection took 76.0 simulated ms
(293.59 wall seconds). Its first new pair arrived after 6.48 ms and completed
at age **38.22 ms**, including 31.73 ms processing. Telemetry had no sequence
gaps at startup. The verified local image ID is
`sha256:dd79824f8dae0c04cce7b844336f570669fdc9a3571ac37c54a868c62c53d2bc`;
cross-store image validation accepted its matching runnable contents. Both hosts
use the same checkpoint index and source fingerprint.

An earlier validation capture with extra image diagnostics enabled did not
complete its freeze handshake and was stopped. Its cause was not established.
The captures and restores above use the experiment's production setting,
`image_diagnostics=false`; diagnostic logging mode is not qualified here.

Controller and both worker checkouts were synchronized, with prior changes
backed up and checked against their now-committed contents. Source revision:
`bd1a8f65cd9da618f9b7095ee433f2846143a7ca`; working-tree fingerprint:
`a2affbb8a47ce66e69a801afc0d46070da495aca35fe3c286b36daeb2b5645f9`.

The original experiment `52c15e35701e4bfbb5de0b3045db219e` was finalized with
all attempt results collected. Its remaining old-binary Pop!_OS retry was stopped
and collected. Both formerly pending original ubuntu-george cases completed
with MISS scores. Historical scores and raw diagnosis remain unchanged.
During evaluation, `case-005` expired 38 of 40 processed pairs and produced no
tracked states or plans. `case-007` expired 21 of 43 pairs, produced three
tracked states, and attempted two plans; both missed their planning deadline.
These late results agree with the earlier freshness and planning diagnosis.

New experiment `07da5b4d9a374221875f24dc2edc7c17`
("Memory contention — perception recovery v2") repeats the original
eight-case matrix with new application and checkpoint provenance. ubuntu-george
prepares the assets; both hosts participate. Future attempts use one worker per
host, four host CPUs and a 12 GiB soft memory reservation. Previous attempts
peaked at roughly 4.5–5 GiB against a 3 GiB reservation. Guest hardware is
unchanged. Scores must be checked after the detailed runs complete.
Both hosts completed preparation and dispatched cases from the shared final
checkpoint. See the [deployment snapshot](perception-recovery-status.json) and
[validation metrics](perception-validation.json).

Preparation `8c9827cc62294e1fba2dc47570915232` was stopped and finalized
before dispatch after review exposed the admission estimate's starvation edge
case. Its frozen inputs were superseded instead of modified.

Local validation artifacts are under
`.chimaera-m5/perception-recovery-20261024/`; this directory is intentionally
ignored and contains large checkpoint assets. Recovery status and selected
small logs are retained in [perception-validation.tar.gz](perception-validation.tar.gz).
Its SHA-256 is recorded in [validation metrics](perception-validation.json).
The two late original results are in [late-results.tar.gz](late-results.tar.gz)
(SHA-256 `5f7016c9d179077808e58ff2e4568cba006fed18d49abeb2df707e03568072c7`).
