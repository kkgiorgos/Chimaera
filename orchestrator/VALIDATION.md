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

## Preparation startup regression — 2026-10-18

Experiment `3707bc5991b14bf5bd63e9457ba69952` failed on `ubuntu-george`
at revision `951d307c9f2b16520ef2df1bb4ba16a7d4413ee3`. The SDK/application build
and guest disk assembly succeeded. The first suite invocation, `--calibrate-only`,
then exited with `run_memory_suite.py: error: Output must be empty or new`.
The prepared directory contained only `preparation.json`: the new progress writer
had created it before the suite's strict fresh-output validation. Calibration and
checkpoint creation never began; the container exited with code 1 without an OOM.

Fresh calibration now permits that single progress file. Unexpected files,
calibration directories and existing suites remain subject to their original
rejection/resume checks. New producer and consumer regression cases invoke the
real suite entry point from the preparation worker, mocking calibration and later
ROS operations. They reproduced both startup failures before the fix and now
verify initialization alongside rejection of stale files and calibration output.
The supervisor also extracts the first CLI cause from saved worker logs when no
checkpoint error is present, exposing early failures in the dashboard and reports.

The focused command from the preceding validation record passed **211 tests**,
including the new startup and early-error reporting regressions. `git diff --check`
also passed. No full simulation was run for this startup-only repair.

Recovery requires matching updated source contents on the controller and workers,
redeploying supervisors and queuing a new experiment. The failed experiment's
source identity remains frozen; retrying it against changed sources is rejected.
This fix changes exported runner scripts and supervisor reporting, so cached SDK
and application images and the constructed guest disk remain reusable. No remote
checkout, experiment state or deployment was modified during this investigation.

## Address-mode checkpoint gate — 2026-10-18

Experiment `cf1d063b1fad4c818e7a6e06db2b045a` on `ubuntu-george`, using
revision `4397a81823631a668d949c7f00e9b6a3960777aa`, reached arm/camera readiness
at 0.4285 simulated seconds. Its checkpoint stage was **Saving guest memory and
device state**, but gem5 aborted before writing a completed checkpoint:

```text
src/cpu/simple/timing.cc:832: TimingSimpleCPU::completeIfetch:
Assertion `_status == IcacheWaitResponse' failed.
```

The bridge's subsequent `timing server disconnected before replying` was a
consequence of that assertion. The diagnostic parser now recognizes simulator
assertions before selecting a cascading bridge disconnect.

The guest restore wait previously called `m5_quiesce_ns_addr(1000)`. Address-mode
m5ops execute inside a local memory access: quiesce suspends the CPU there, but
the access completion continues instruction execution, and the scheduled wakeup
can initiate another fetch while a response is pending. The capture gate now
polls the readfile without quiescing the CPU. It still performs no transport I/O
before restore and can drain at an instruction boundary.

The extended `checkpoint_barrier.cc` workload and new
`verify_checkpoint_barrier_fs.py` exercised the real Linux kernel/backing disk,
four KVM boot CPUs, four detailed Timing CPUs, MESI and Ramulator. The test waits
for the workbegin CPU switch to complete before checkpointing, then requires
sixteen actual polling iterations before saving; this prevents a smoke test from
passing before the problematic operation executes.

In disposable containers on `ubuntu-george`, the old `--quiesce` gate reproduced
an instruction-fetch scheduling assertion in `IcachePort::recvTimingResp`
(`!tickEvent.scheduled()`). The polling gate passed and printed
`CHECKPOINT_BARRIER_FS_GATE_ACTIVE` and `CHECKPOINT_BARRIER_FS_SAVED`.
A separate restore container received **no `/dev/kvm` device**, instantiated only
the detailed CPUs, verified the saved tick, and printed
`CHECKPOINT_BARRIER_FS_RESTORED`. The restored guest also exported
`CHECKPOINT_BARRIER_RESTORE_OK` through m5 writefile, avoiding serial transmission
latency as a test oracle. Test records remain under
`/tmp/chimaera-checkpoint-validation-20261018/full-system-20261018/`.

The focused Python/native helper command above passed **212 tests**. The changed
guest bridge passed a syntax check in the production Ubuntu/Humble image, and
the instruction-mode SE checkpoint regression also passed. `git diff --check`
passed. The worker checkout, backing disk and experiment state were mounted
read-only or left untouched; no deployment was performed. This validates the
address-mode Linux capture/restore gate, not a complete robot replay/throw campaign.

This repair changes the compiled guest bridge and requires updated SDK and
application images, supervisor deployment and a new experiment with matching
controller/worker sources. Source-based image caching detects the changed bridge;
old guest bundles and readiness checkpoints must not be reused.

## Bounded Linux/ROS checkpoint lifecycle — 2026-10-18

`containers/ball-catching/checkpoint-smoke.py` builds the current production
bridges and a small real ROS application in an isolated overlay. The application
publishes readiness after receiving a request and stores a sequence counter in
guest memory. Capture must return sequence 1. Each independent restore receives
a different request and must return sequence 2; neither a fresh boot nor a
replayed response can satisfy that check. Linux, four Timing CPUs, MESI,
Ramulator, bridge transport, Gazebo host replay and the physical launch gateway
are production components. Readiness is supplied by the small ROS fixture;
camera rendering and the full robot controller are omitted.

Each simulation is limited to 30 ms of actual detailed ticks and has a
180-second wall watchdog. `--skip-packaging` tests capture, two independent
KVM-free restores and preparation report collection. The complete mode also
tests a restore before image export, the production checkpoint image packaging
and import paths, changed-index rejection, and two restores using imported assets.
Scoring a full catch remains outside this checkpoint regression; its host bridge
stops after the short budget, before the synthetic application's throw deadline.

The combined fixture exposed two additional correctness problems that the earlier
static Linux gate workload did not cover:

- With the original functional writeback, restore aborted on the first heap-backed
  bridge payload: `fatal: readBlob(0x3b4187c0, ...) failed`. Copying only
  `RubyRequestType_ST` records misses modified data downgraded to MESI shared
  state. The writeback now resolves every recorded address through Ruby's
  coherent functional read path and copies each unique cache line into backing
  RAM before saving a cold-cache snapshot. Existing checkpoints cannot recover
  bytes omitted by the old writeback.
- Checkpoint drain advances guest time past the recorded host boundary. Starting
  pressure immediately allowed the configured 1 ms hold to finish while gem5
  remained paused. The host now catches up before enabling pressure. The fixture
  checks actual gem5 warmup ticks before measurement, in addition to launch and
  preserved-state communication.

The full-size disk also exposed packaging/import space amplification. Packaging
now uses a compressed sparse PAX archive and a single Docker layer, setting modes
in archive headers. The older GNU sparse header is rejected by Docker and is
covered by the regression. Import streams Docker's tar output and recreates zero
regions as sparse file holes, verifying the original full disk hash afterward.
Tests cover densely allocated zero regions, export failure, traversal and links.

The fixed simulator compiled on `ubuntu-george` using retained SCons objects in a
disposable Docker build. The candidate application image is
`sha256:9e460d6f1f6a23b9ef2df7913be818adfdc793d5cae33b31efa425ef5d33ba1a`.
An immediate restore from its fresh checkpoint passed with **18.834 ms capture**
and **29.166 ms restored execution**. Capture took **90.9 seconds** including
integrity checks; the restore phase took **75.9 seconds**, with **48.1 seconds**
inside co-simulation. It reproduced 27 native host intervals, aligned the host in
11 intervals, verified a physical launch, returned the fresh sequence-2 response,
and measured **1.5 ms of actual pressure warmup**. The restore container had no
`/dev/kvm` device.

The fresh short command completed successfully in **274.6 seconds (4 min 35 s)**:

| Phase | Wall time | Actual detailed time |
| --- | ---: | ---: |
| Build current bridges and fixture | 37.9 s | — |
| Boot, capture and verify | 87.3 s | 15.847 ms |
| Independent restore, request 2 | 74.3 s | 29.153 ms |
| Independent restore, request 3 | 75.0 s | 29.153 ms |
| Collect preparation report | 0.06 s | — |

Both restores received no KVM device, returned the expected sequence-2 state,
verified the physical launch, and measured 1.5 ms of real pressure warmup. The
production controller's SSH collection path also fetched this report successfully
in **0.52 seconds**, including checkpoint metadata, launch logs and guest serial.
No checkpoint RAM or backing disk was included in the diagnostic download.

The complete image workflow also passed: **294.3 s packaging**, **37.2 s sparse
import**, changed-index rejection, and independent packaged restores in **76.5 s**
and **75.9 s**. Both ran 29.166 ms of detailed time and passed the same state,
warmup and physical launch checks. The resulting readiness image is
`sha256:da70b3570ebd8c16490e8160326e445fe9d9275bb88e36389a197172685d2733`, with
index SHA-256 `c80a542ef04ae9096826db64727f4aecaebb915d26de0f76681e4ca5240d596c`.
This workflow reused its fresh capture after correcting the test's final-tick
reporting: restored execution uses `finalTick` in `stats.txt`, since the restore
entry point does not print the boot path's final-tick log line. The separate
short command above passed from a new empty output directory without resuming.

Worker records are under
`/home/kkgiorgos/Chimaera/.chimaera-orchestrator/checkpoint-smoke/`:
`smoke-20261018-5` contains complete packaging/import results;
`smoke-20261018-6` contains the short command's passing `smoke-result.json`.
Small summaries and real SSH-collected reports were retained locally under
`/tmp/chimaera-checkpoint-smoke-collected-20261018/`. Obsolete invalid test images
and duplicate imported test disks were removed; compiler caches and experiment
assets were preserved.

The focused regression suite passed **219 tests**. The changed gem5 binary and
production ROS bridges compiled successfully; syntax checks and
`git diff --check` passed. These fixes are validated in isolated containers and
have not been deployed to the controller, supervisor or production SDK images.
Deployment must include the simulator and both bridges, followed by newly
generated readiness checkpoints. Earlier readiness checkpoints must be regenerated
with the coherent writeback fix.
