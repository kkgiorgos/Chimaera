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
There is no coordinated simulation checkpoint support; resume is at case boundaries.

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
