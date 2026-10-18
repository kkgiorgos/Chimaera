# Validation record — 2026-10-15

Branch: `feat/ball-catching-orchestrator`.

## Automated checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  orchestrator/tests containers/tests containers/ball-catching/tests
```

Result: **57 passed**. This includes a simulated three-machine, ten-case workflow
through preparation, shared calibration transfer, dynamic dispatch, result
collection, and completion. Failure/recovery tests cover lost SSH replies,
controller/supervisor reopen, explicit retry, stop generations, stop/collection
races, queue blocking, and stop priority while transfer/dispatch pools are busy.
Shared calibration tests confirm reuse without a simulator invocation and
rejection when the simulator digest differs.

`npm run typecheck` and the Next.js production build passed. `npm audit --omit=dev`
reported **zero vulnerabilities** after overriding Monaco's compatible DOMPurify
dependency to its patched release.

Both Compose configurations passed `docker compose config --quiet`. The production
webserver stack built and started using an isolated project named
`chimaera-orchestrator-check`, port 3101, a dedicated test data volume, and an empty
SSH directory. Machine settings, experiments, and notification preferences survived
container recreation.

## Browser and Docker checks

Headless Chromium exercised the packaged stack:

- Save a machine with the default 1 CPU / 2 GiB worker settings.
- Load locally bundled Monaco and validate the ten-case default experiment.
- Queue and stop an experiment on an unconfigured test host, without SSH deployment.
- Change the progress notification interval and verify the API state.
- Download and read a valid result archive containing configuration and run metadata.
- Toggle both themes and verify appearance persistence after reload.
- Verify readable light-theme action text and a 390 px mobile viewport without
  page-level horizontal overflow. The case table scrolls inside its own bounds.

There were no browser page errors in the passing workflows.

The checked-in `tests/docker_lifecycle.py` also passed against the final supervisor
image using real, short-lived Docker containers with synthetic Python workloads.
It verified detached completion after reopening supervisor state, idempotent case
assignment, stdout/stderr capture, immediate stop, rejection of late dispatch, and
cleanup. It did not invoke gem5, Gazebo, ROS, or Discord.

## Validation boundary

No real SSH machine credentials or Discord webhook were configured. Deployment to
remote hosts, independent full SDK builds with identical simulator hashes, large
asset transfers, fresh guest boot, KVM instantiation, and complete robot benchmark
execution have **not** been qualified on the destination cluster. The guest
construction path is implemented but requires that boot/full-system validation.
Ready guest checkpoints plus a validated host input replay now support starting
cases after detailed, aggressor-free readiness. This protocol still requires
full-system restore/throw qualification on the destination cluster. It does not
resume interrupted throws in place.

ETAs are estimates from live throw speed and observed case durations. They have not
yet been calibrated against real distributed runs and remain unknown when the
necessary measurements are absent. Controller and UI scaffolding checks do not
substitute for that simulation validation.

## Collection recovery and local build regression checks

The updated combined pytest suite passed **74 tests**. Added coverage includes
failed downloads without automatic retry loops, explicit collection retry without
rerunning a case, cancellation during deletion, prevention of late file
resurrection, startup recovery of older failed/collecting cases, supervisor
handling of missing output directories, and HTTP-safe server-issued machine IDs.
Build tests verify separate SDK/application cache identities, inclusion of ignored
robot sources, exclusion of generated build output, local guest preparation on
every host, and strict calibration/kernel validation across independent disks.

`npm run typecheck`, `npm run build`, and `git diff --check` passed. A Chromium
regression against the production web build added a third machine with
`crypto.randomUUID` unavailable, displayed a collection error, retried collection,
and deleted the experiment during a blocked download. The download was cancelled,
deleted files stayed deleted, and there were no browser page errors.

The updated supervisor image built successfully and passed the real Docker
synthetic lifecycle check, including detached completion, supervisor reopen,
duplicate prevention, stdout/stderr capture, immediate stop, stop barrier, and
cleanup. These checks did not run simulations or deploy to the destination SSH
cluster; the simulation validation boundary above still applies. Guest disks and
Docker images are now built locally on each machine rather than transferred.

## Guest-init symlink repair

Local run inspection found four cases exiting with startup timeouts after the
guest booted systemd to a login prompt. The exported Ubuntu image's init is an
absolute symlink; the assembler now removes that symlink before installing the
executable Chimaera init script. It previously followed the link into the helper
filesystem, leaving the guest's systemd startup in place.

The combined pytest suite passed **77 tests**, including existing/dangling init
symlink targets and guest recipe cache invalidation without deleting older disks.
The updated supervisor image built successfully. A disposable fixture assembled
inside that image passed real tar, mke2fs, e2fsck, and dd operations. Inspection
with debugfs verified the ext4 image contains a regular, mode-0755 init with the
expected script, and the symlink's former target was unchanged. Host-native
assembly was not used because its older mke2fs lacks the production feature flags.

This repair has not yet been validated with a full guest boot or benchmark run.
Running workers and existing experiment data were left untouched. Existing
prepared experiments retain their original disks; a new experiment after updating
and redeploying supervisors uses the corrected recipe cache entry.

## Readiness checkpoints — 2026-10-16

The final Python workflow checks passed **168 tests**:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=ros/ball_catching_ws/scripts:orchestrator/backend \
python3 -m pytest -q orchestrator/tests containers/tests \
  containers/ball-catching/tests \
  ros/ball_catching_ws/tests/test_detailed_start.py \
  ros/ball_catching_ws/tests/test_readiness_checkpoint.py \
  ros/ball_catching_ws/tests/test_checkpoint_replay.py
```

Coverage includes producer-only KVM, shared checkpoint/bundle/disk identity,
compatible ball variations, corruption rejection, fixed pressure warmup, no
aggressor during readiness, and refusal to launch checkpoint preparation.
The Ubuntu 22.04/Humble/Fortress container build of the changed native code passed
**3 transport, 6 bridge and 3 ball CTests**, plus **86 ROS Python checks**
(two optional simulator-instantiation checks skipped in that ROS-only invocation).
`npm run typecheck` and `git diff --check` passed.

The real Fortress `checkpoint_replay` CTest captures host readiness input and
replays it into a fresh host while the synthetic guest stays paused. It checks
saved joint state/time, a different ball speed, the pressure barrier, and a throw
after the fixed hold even when the guest reports no longer ready under pressure.
Real Docker checkpoint-image packaging, exact asset import, and cached import
also passed with disposable fixture artifacts.

Populated-cache testing exposed unsupported MESI flushes and a stalled MESI
warm-cache trace restore. The opt-in checkpoint path writes drained dirty owners
into backing RAM and saves an empty cache trace. Cases restore CPU/RAM state with
cold caches and use their configured short warmup to repopulate them; baselines
use the same policy. Other Ruby clients retain their existing default behavior.

The final SDK built successfully. The native gem5 regression
`ros/ball_catching_ws/tests/verify_checkpoint_memory.py`, with its statically
linked `checkpoint_memory.cc` workload, saved 1 MiB of dirty data, verified every
64-bit value directly in compressed checkpoint RAM, and resumed without KVM to
print `CHECKPOINT_MEMORY_OK` and `CHECKPOINT_MEMORY_RESTORE_OK`. This verifies RAM
independently of cache reconstruction. A full-system CPU/device topology smoke
check initialized KVM, switched to detailed CPUs, populated caches, checkpointed,
and restored without KVM at the saved tick; pressure/measurement barriers passed.
That smoke check did not boot Linux through application readiness. Timing and O3
restore graphs also instantiated without KVM objects. A real Ramulator check
accepted zero aggressor requests while disabled and generated traffic only after
explicit enable.

These checks do not establish numerical equivalence to the previous protocol or
qualify a complete robot guest readiness/restore/throw campaign on the cluster.
Host replay reconstructs Gazebo from recorded input; it does not serialize its
renderer/physics internals. Fresh experiments are required after deployment;
in-flight throws are not resumable.

## Preparation visibility and checkpoint drain barrier — 2026-10-18

Inspected the failed preparation on `ubuntu-george` for experiment
`a8499008407c4e759dca3a63d2eebadb`. Its readiness records reach arm and fresh-camera
readiness at 0.417 simulated seconds. The last timing sample is exchange 833 at
2389.34 wall seconds. `checkpoint-ready` and the saved host state exist, but no
completed guest checkpoint exists. The final guest packet is a new `CHIMCTR2`
handshake; the capture host is still serving message batches. The bridge reports
`gem5 timing: socket timeout; simulator state is unknown` after its 900-second
command deadline, followed by gem5's cascading connection-reset panic. This is
separate from the configured 43,200-second attempt limit. The preparation container
exited with code 1 and was not OOM-killed.

The guest now waits on the checkpoint readfile marker after issuing its m5
checkpoint operation. It performs no transport I/O if gem5 resumes it during
draining. Restore replaces that marker with the simulation origin before the
guest resumes and starts its new handshake. The dashboard receives preparation
stages, elapsed time, checkpoint counts, timing/speed, and arm/camera readiness.
Checkpoint failures retain their error and small diagnostic records for collection.

An isolated, non-networked container on `ubuntu-george`, using its existing
application image, passed a syntax check of the changed guest bridge. The new
`checkpoint_barrier.cc` workload and `verify_checkpoint_barrier.py` ran against
the real gem5 Timing CPU, MESI and Ramulator configuration. Capture printed
`CHECKPOINT_BARRIER_SAVED`; a separate restore invocation printed
`CHECKPOINT_BARRIER_RESTORE_OK` and `CHECKPOINT_BARRIER_VERIFIED`. Test inputs and
outputs were confined to `/tmp/chimaera-checkpoint-validation-20261018`; the worker
checkout was mounted read-only. No deployment or experiment data was changed.

The web production build and TypeScript check passed. **193 focused tests passed**:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=ros/ball_catching_ws/scripts:orchestrator/backend \
python3 -m pytest -q orchestrator/tests containers/tests \
  containers/ball-catching/tests \
  ros/ball_catching_ws/tests/test_detailed_start.py \
  ros/ball_catching_ws/tests/test_readiness_checkpoint.py \
  ros/ball_catching_ws/tests/test_checkpoint_replay.py \
  ros/ball_catching_ws/tests/test_progress_monitor.py \
  ros/ball_catching_ws/tests/test_benchmark_status.py
```

These checks cover
producer/consumer workflows, checkpoint integrity, native barrier/replay tests,
readiness progress through controller persistence, failure diagnostics and bounded
shutdown on bridge failure. The broader `test_memory_benchmark.py` invocation on
the local ARM host could not complete because ROS `ament_index_python`, the
installed application package and PyYAML are unavailable there (43 passed, 17
dependency failures). A full Linux guest readiness/restore/throw campaign remains
unvalidated; the isolated native regression does not replace that campaign.
