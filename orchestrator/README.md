# Ball-catching orchestration

A self-hosted Next.js dashboard and persistent scheduler for the current
ball-catching memory-contention benchmark. One experiment runs at a time; cases
are dispatched from a shared queue to available worker slots on selected machines.
Queued experiments wait behind unresolved failures.

## Deploy on the webserver

Requirements:

- Docker Engine with Compose, and an up-to-date checkout of this repository.
- Linux x86-64 workers with Docker Engine/Compose, writable repositories,
  and permission for the SSH user to use Docker and the guest assets.
- KVM on the selected preparation machine for its initial guest boot. Case
  workers restore detailed checkpoints and do not need `/dev/kvm`.
- Bash and gzip on workers for compressed image streams. The producer also needs
  Python 3 and an OpenSSH client on its host.
- SSH key access from the webserver to each worker. No inbound worker HTTP ports
  are required. Each image producer must also have noninteractive SSH key access
  to its destination workers, with verified host keys in its own `known_hosts`.
- Internet access on workers for initial image builds and the pinned kernel.

Create a **dedicated** SSH directory containing an identity, `known_hosts`, and
optionally an SSH `config`. Host keys must already be verified: connections use
`BatchMode=yes` and `StrictHostKeyChecking=yes`. An SSH config can select identities
or a jump host. Paths inside that config should use `/root/.ssh/...`.

From this directory:

```bash
cp .env.example .env
# Set CHIMAERA_SSH_DIR to the absolute dedicated SSH directory.
# Optionally set DISCORD_WEBHOOK_URL.
docker compose --env-file .env up -d --build
```

Open <http://localhost:3000> on the webserver. The default bind is loopback; use
your existing VPN reverse proxy or set `CHIMAERA_BIND` to the server's VPN address.
There is no application login: access control belongs to your VPN/proxy.
The controller has no published port and no webserver Docker socket mount.
Next.js uses its [standalone Docker output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output).

### Tailscale worker names after a host reboot

If workers use MagicDNS names such as `pop-os`, add these settings to `.env`,
using the `MagicDNSSuffix` from `tailscale status --json` on the webserver:

```dotenv
COMPOSE_FILE=compose.yaml:compose.tailscale.yaml
CHIMAERA_TAILNET=tail123456.ts.net
```

Run `docker compose --env-file .env up -d` from this directory to recreate the
controller with the persistent settings. The override explicitly sets Tailscale's
`100.100.100.100` resolver and the tailnet search domain. This avoids inheriting
host DNS settings before Tailscale finishes starting during a reboot, and restores
short-name lookup in Podman networks whose default search domain is `dns.podman`.
Tailscale and MagicDNS must be enabled on the webserver; worker polling retries
connections while the VPN starts. Container service-name lookup stays on the
Compose network's internal DNS.

Use the same `.env` for subsequent Compose commands. If passing `-f` explicitly,
include both `-f compose.yaml -f compose.tailscale.yaml`, because `-f` overrides
`COMPOSE_FILE`. A plain container restart cannot apply new DNS settings; recreate
the controller once with `up -d`. This keeps the controller data volume.

The named `controller-data` volume holds SQLite state, queued commands,
notification delivery records, frozen calibration/provenance, temporary result downloads,
and collected results. Keep that volume when updating containers. Back it up
with the service stopped or use SQLite's backup API; do not copy only the database
file while its WAL is active. `docker compose down` retains the volume;
`down -v` deletes it.

## Connect machines

1. Choose **Add machine**, supply its SSH target and absolute repository path,
   and choose its worker count. The default worker is **1 CPU / 2 GiB RAM**.
2. Choose **Deploy supervisor**. The controller runs the repository's
   `worker.compose.yaml` over SSH, building the small supervisor image locally.
3. Wait for the machine to appear online. Repeat for the other machines.

Supply an existing guest disk/kernel under **Build and guest assets** if desired.
Leave these fields empty to prepare them automatically. Choose the
**Image preparation & calibration machine** when creating an experiment; it must
be one of the selected machines.
That machine produces the frozen calibration reference, SDK image, and application
image. The producer streams both exact images directly to the other workers over
authenticated SSH. The controller coordinates transfers and receives progress;
image bytes never pass through the webserver. Configure the destination SSH targets
(including aliases, ports, identities, and any jump hosts) to work from the producer
host as well as the controller. Producer credentials and SSH configuration belong
to its host SSH user; the controller does not copy credentials or forward an agent.
The preparation machine uses its configured assets or constructs a guest disk
and downloads the pinned kernel. Its final application image includes the exact
backing disk, kernel, guest bundle and readiness checkpoints. Consumers import
these assets from that image; independently generated disks cannot restore the
producer's checkpoint. Consumer disk/kernel overrides are therefore unused.

Docker image stores can assign different image IDs after a save/load transfer.
When IDs differ, transfer verification compares the producer and destination's
runtime configuration, uncompressed filesystem layer digests, and platform.
Different or missing content fails preparation. Each consumer uses its verified
local image IDs; the producer's frozen calibration identities remain intact.
A previously loaded transfer tag is reverified before reuse, avoiding another
download after a successful load. After updating the controller for this fix,
use **Retry preparation** on a worker that reported an image identity mismatch.

If preparation reports **Repository revision or source contents differ from the
queued experiment**, the worker checkout differs from the source identity frozen
when the experiment was queued. Transferred images do not replace that checkout.
The error lists queued and local revisions/fingerprints. Restore the queued
revision and matching source contents before choosing **Retry preparation**.
If adopting newer code, stop the old experiment, align the controller and all
worker checkouts (including relevant uncommitted/untracked sources), rebuild the
controller, redeploy supervisors, and queue a new experiment. Redeployment and
retry do not refresh an existing experiment's frozen identity. Keep cached images;
matching build inputs can reuse them in the new experiment.

CPU settings use Docker quotas, not exclusive core pinning. RAM per worker is a
soft budget (`--memory-reservation`), with no Docker hard memory or swap limit.
Workers can exceed it instead of being killed at the configured size. The
supervisor samples Docker memory usage, retains the highest observed usage, and
warns in the case table, activity feed, and configured Discord notifications once
per attempt when usage exceeds the budget. Warnings persist after completion and
controller/supervisor restarts. Peaks between samples may be missed. Increase RAM
per worker or reduce worker count for future runs when warnings appear. Host-wide
memory exhaustion can still cause an OS kill; a soft budget does not reserve
physical RAM or prevent overcommit. Standalone container runners retain their hard
limits. Existing containers keep their original limits until a fresh attempt.
You choose worker counts and CPU quotas; there is no automatic tuning policy.
Changes to worker count/size apply to future assignments. Existing cases retain
their launch limits. Connection/repository/asset paths cannot change during an
active experiment. Build parallelism is separate from case worker limits.

The supervisor mounts its host repository at the **same absolute path** inside
the container. This is necessary because sibling workers' Docker bind mounts are
resolved on the host. It runs as the SSH user's UID/GID, with access to the host
Docker socket. Only preparation on the producer receives the KVM device. This trusted supervisor controls Docker;
benchmark cases retain the existing runner's read-only mounts, private network,
private IPC, resource limits, and dropped capabilities.

Remote state lives in `<repository>/.chimaera-orchestrator/`, including the
supervisor's SQLite database, frozen inputs, assets, and attempt directories.
Do not remove it while cases are active or resumable. Re-deploying the supervisor
does not remove running sibling workers.

## Configure and run

Choose **New experiment**. Monaco opens the current
`memory_contention_patterns.json` template with the matching Catppuccin theme.
Import/export JSON, edit it, select workers and an image preparation/calibration
machine, then **Validate** to preview the expanded cases. The server uses the benchmark's real validator; the editor also
provides JSON schema diagnostics. `ramulator_config` is relative to the repository
root and must resolve to a file inside that repository.

**Queue experiment** freezes the configuration and source identity. When its turn
arrives, preparation runs automatically:

1. On the chosen calibration machine, validate the queued source identity and build
   or reuse its source-keyed SDK and application images.
2. Prepare its guest assets, run memory-only calibration and fractional-rate
   checks, and freeze the guest bundle. Boot Linux once per compatible readiness
   configuration with KVM, switch to the selected detailed CPU at workbegin, and
   reach arm/fresh-camera readiness with the aggressor disabled. Save the guest
   at a completed bridge exchange and record the host readiness prefix. No ball
   is thrown during preparation. The default matrix produces one checkpoint.
3. Package the checkpoints and exact backing disk/kernel/bundle into the application
   image. Record both immutable Docker image IDs with the calibration. Stream those images
   with `docker image save`, gzip and `docker image load` over a direct SSH
   connection from the producer to each remaining worker. Skip each transfer when that exact image already
   exists there. Archives carry a dedicated `chimaera:transfer-<image-id>` tag so
   loaded images retain a named reference. Imported images do not replace
   source-keyed local SDK tags.
4. Verify the image ID after loading. Consumers validate their source keys, use
   the copied application image, import the producer's frozen assets, and validate
   calibration, checkpoint integrity, simulator, kernel, disk and bundle hashes.
   Consumers run no robot readiness simulation and receive no KVM device.
5. Dispatch cases dynamically as workers become ready. The calibration machine can
   run cases while SDK copies and other workers' preparation continue.

Every case restores the same compatible, aggressor-free detailed guest state.
While gem5 stays paused, its host replays the recorded readiness commands at native
speed to reconstruct Gazebo time, robot motion and readiness. A mismatch in saved
time or joint positions/velocities fails the case. This is a host input replay,
not a serialized snapshot of Gazebo's renderer or physics engine internals.
The restored guest reconnects to a fresh bridge. Drained dirty cache data is
preserved in RAM; MESI caches restart empty to avoid its unsupported flush and
warm-cache reconstruction paths. Case pressure then runs for
`fixed.pressure_warmup` (default **0.05 simulated seconds**, at most one second,
rounded up to complete coupling intervals). The host resets measurement statistics
at the throw boundary and launches without repeating the readiness gate under
pressure. This hold also repopulates caches. Baselines receive the same hold with
the aggressor disabled.

Ball position, direction and speed remain host-only launch settings, so all throws
with the same hardware/camera/controller configuration share a checkpoint across
pressure levels and access patterns. Changed hardware, memory, camera setup or
controller rates require compatible checkpoints; preparation groups those separately.
Missing or incompatible checkpoints fail preparation/execution; there is no KVM
fallback. Use a new experiment after updating the simulator/bridge for this protocol.

The dashboard shows **Copying prepared images** during transfer, with the current
SDK/application image, compressed bytes sent, average transfer rate, elapsed time,
and an estimated ETA for the current image based on its uncompressed Docker size
and measured export/network throughput. The ETA excludes loading and verification;
those stages display their status instead. ETA appears after throughput is sampled.
The report also shows the cache-check, streaming, loading, or verification stage. Byte counts measure
data forwarded by the producer to the destination SSH connection; loading can continue after all
bytes have been sent. A percentage is not shown because the compressed archive
size is unknown until export finishes. Verification errors include Docker's load
output, and streaming errors distinguish source export from destination load.
Transfer failures are preparation failures with **Retry preparation**. Stop cancels active transfers;
controller restarts recheck the destination cache and repeat incomplete transfers.
Transfers have a one-hour limit and require verified destination SSH host keys on
the producer. The producer stops its export and destination connection when the
controller cancels or disconnects. No image archive is retained; compressed bytes
travel directly from producer to destination once per worker that lacks the image. Old experiments without an explicit
calibration-machine choice retain their original first-machine default.

The case table shows simulation speed as a real-time
multiplier (simulated seconds per wall-clock second), averaged through the latest
timing sample. Before timing data arrives it shows **Awaiting sample**. Stale
active samples are labeled **last sample**, and finished cases retain their last
recorded speed.

Preparation also shows its current calibration, bundle, or checkpoint stage and
total elapsed wall time. During readiness it reports the current checkpoint out of
the total, simulated time, completed exchanges, simulation speed, arm readiness,
fresh camera readiness, and extra warmup when configured. After readiness it shows
the guest barrier and memory/device serialization stages. Stale timing samples
remain labeled **last sample** while checkpoint saving proceeds; readiness has no
predictable percentage or ETA. Failed preparations display their bridge/guest
error, and collected preparation reports include checkpoint launch logs, guest
serial, timing, and readiness records without copying checkpoint RAM.

The guest stays off the transport while gem5 drains its checkpoint and only
starts a new handshake after the restoring simulator releases it. A timing socket
timeout during capture is a preparation failure; increasing `startup_timeout_s`
does not fix a transport barrier deadlock. After updating this protocol, align
controller and worker sources, rebuild the controller, redeploy supervisors, and
queue a new experiment so its guest bundle and images contain the fix. Retrying
an old experiment retains its frozen source identity.

SDK image cache keys depend on the content of simulator, transport, bridge, stack
profile and build inputs. Application image keys additionally depend on the robot
application sources and the exact SDK image ID. Host editor caches (`.cache`, `.vscode`), simulation output
(`m5out`, `m5_outputs`), and generated parser tables are excluded from both cache
keys and Docker build contexts. Repository commits, dashboard changes, experiment parameters,
worker sizes and build parallelism do not invalidate the SDK image. If its image
is already present, preparation skips gem5 compilation entirely. A missing image
uses Docker layers and the existing SCons build cache; a simulator or dependency
change can require recompilation. Generated guest disks are also cached locally
by image identity and guest-construction recipe.

Calibration measures the **simulated** memory system's sustainable bandwidth for
each pattern. A 50% pressure case requests half that pattern's calibrated rate;
it is unrelated to the physical worker's RAM bandwidth. Reuse requires matching
simulator/configuration hashes. Independent simulator builds use a fixed compiler
date via `SOURCE_DATE_EPOCH`; genuinely different binaries/dependencies still fail
closed and expose the preparation error. The fixed date does not pin packages
installed from live APT repositories: separately built SDKs can have different
dependencies and simulator binaries even when their source keys match. To reuse a
calibration reliably, the orchestrator copies the calibration SDK and application
image automatically using immutable identities. A missing copied
image fails preparation instead of triggering an independent build. No worker
silently recalibrates.

If preparation reports a shared calibration provenance mismatch, compare the
reported calibration and local values. A `gem5_sha256` difference means the actual
simulator binaries differ; matching Git revisions alone is insufficient. A build
source mismatch is rejected before Docker builds or guest preparation. These keys
also include ignored external source trees and executable permissions. Align those
inputs across machines. If build keys match but binaries differ, compare build
options and dependency versions, and reuse the same compatible SDK image on all
workers. Rebuilding only a consumer does not change the producer's frozen reference.
If you intentionally change simulator inputs, start a new experiment and produce a
new calibration using the build that all workers will use. Keep the existing
calibration records intact; editing their hashes would hide incompatible builds.

The generated guest uses the application image's Ubuntu/ROS filesystem, a generic
root init/readfile boot path, a partition-2 ext4 root, and this repository's m5 tool.
It is deliberately larger than a minimal runtime-only image and uses sparse disk
storage. The kernel is a pinned prebuilt resource, not a new Linux source build.
The guest disk is read-only during experiments; gem5 creates per-case COW overlays.

## Progress, recovery, and logs

The dashboard polls the scheduler every three seconds; supervisors are sampled
about every five seconds, subject to SSH and Docker sampling latency. Machine rows
show active assignments, Docker CPU/RAM usage, readiness/throw progress, and ETA.
Stopped/unreachable machines are shown explicitly; stale progress does not acquire
a new ETA.

- Case ETA uses observed throw speed once the ball launches, or prior completed
  case durations for the same machine and worker size. Throw estimates exclude
  finalization; historical estimates are deliberately labeled approximate.
- Whole-experiment ETA schedules remaining work over measured worker slots. It
  excludes preparation/collection/reporting, and remains unknown when data is
  insufficient. First-time readiness and boot do not have invented percentages.
- A finished container frees its worker slot while results are still transferring.
- Supervisors automatically remove completed, failed, and stopped preparation and
  case containers after saving worker stdout/stderr. Results, preparation reports,
  and saved logs remain available for collection and viewing. Failed log capture
  or container removal is retried on later supervisor polls, including after a
  restart; existing terminal tasks are cleaned up too. Running containers are
  never forcibly removed by this cleanup.
- The log viewer offers worker stdout/stderr, ROS/Gazebo launch output, guest serial,
  and gem5 statistics. Collected case logs also work without the remote machine.

Active cases continue through dashboard restarts and SSH disconnects. Supervisors
save assignments before creating detached Docker containers and reconcile by the
same attempt/container ID after lost replies. While the webserver is offline,
finished workers wait; no new cases are dispatched. Machine reboot/container
failure does not trigger an automatic benchmark restart.

A failed case notifies Discord while other cases continue. Catch and miss both
count as successful benchmark results. At the end, failures hold the experiment
queue until you **Retry failed cases** or **Accept partial results**. Preparation
failures have separate log and manual retry controls.

Case downloads exclude the shared `suite/calibration` link, whose target exists
only inside the worker container. The frozen calibration is included separately
in the experiment's `calibration.json`; per-case guest input bundles are also
excluded. This collection policy applies to existing attempts, so **Retry
collection** can recover their results without rerunning simulations.

Case execution and collection are separate states. A failed SSH download or missing
output directory is marked **Result collection failed**, with the error visible in
the dashboard. It is not retried on every poll. **Retry collection** downloads the
existing attempt again without rerunning its simulation. You may delete an idle
experiment even when collection is still in progress or has failed. Deletion
cancels its SSH download and prevents late callbacks from restoring deleted files.

**Stop** kills reachable workers immediately. Stop is pending for unreachable
machines, and the queue remains held until they acknowledge it. An assignment
arriving late is rejected using a persisted stop generation. **Resume** preserves
completed cases and queues the others with fresh attempts. Interrupted cases
restart from their prepared readiness checkpoint; in-flight throws are not
checkpointed. Old attempts remain in the result package.

To stop one worker, use its **Stop** button on the machine card or case row.
Only the selected attempt is terminated; other cases continue and queued cases
can use the freed slot. Its status stays **Stop pending** until the supervisor
acknowledges it, including when the machine is offline or the controller restarts.
The supervisor rejects delayed launch requests for that stopped attempt. Logs
and partial output are collected normally. A completed result that wins the race
with a stop request is preserved. Individually stopped cases remain stopped until
their own **Retry** action starts a fresh attempt; **Retry failed cases** leaves
them stopped. Once other work finishes, stopped cases hold the experiment queue
until retried or partial results are accepted.

## Notifications and results

Set `DISCORD_WEBHOOK_URL` in `.env`, then recreate the controller container. The
URL remains server-side and is never returned to the browser.

Discord messages use Markdown headings and labeled fields, with event timestamps,
experiment and case IDs, case parameters, machine names, worker allocations,
elapsed time, exit codes, memory-limit failures, and collection status when available.
Experiment summaries include counts by case state; periodic updates also include
the estimated remaining time and its basis. Details are captured when the event
occurs, so delayed delivery retains the original context. Long messages omit
trailing fields with an explicit marker; full details remain in the orchestrator.

Notifications cover case starts/completions/failures, experiment completion/stop,
and operational failures. Periodic progress/ETA messages default to **60 minutes**;
enable/disable them or change the interval live in **Notifications**. A persistent
outbox retries webhook delivery without retrying benchmark cases. Delivery is
at least once: a crash after Discord accepts a message can produce a duplicate.

**Download collected results** streams a `.tar.gz` containing the frozen experiment,
calibration/provenance, preparation diagnostics, run/attempt records, full collected
case outputs/logs, per-case reports, and an aggregate comparison report when any
case completed. In-progress transfers are excluded. Large base disks, container
images, per-case guest input bundles, and the repository itself are not included
in result transfers or the result archive.

**Open comparison report** combines the workers' already embedded robot models and
trials into an offline HTML report; the webserver does not require ROS.

**Clean remote copies** removes this experiment's remote inputs, attempt files,
and stopped containers. The confirmation explicitly allows discarding uncollected
remote output when a download failed. Shared image/guest caches stay available
for later experiments.
**Delete experiment** cancels collection and removes webserver result files,
retaining history marked deleted. Active workers must be stopped first; collection
completion is not required. Remote copies can be cleaned before or after deletion.
A cleaned/deleted run must be cloned for a new execution rather than resumed.

## Reclaim unused machine assets

Choose **Clean unused assets** on a machine card when that machine has no active
experiment. It removes unused orchestrator SDK/application Docker images (including
imported images recorded by new preparations), generated guest disks, and their
cache metadata. The card reports removed image/disk counts and allocated guest
disk space reclaimed; Docker layer savings depend on shared layers.

Saved remote experiments, including stopped and failed preparations, retain their
image and disk dependencies so retries and resume continue to work. Docker
container dependencies and explicitly configured disk/kernel paths are preserved.
To release old experiment dependencies, collect results and use **Clean remote
copies** first, then **Clean unused assets**. Results on the controller stay intact.
Cleanup fails if saved dependency manifests cannot be read safely.

Cleanup is limited to orchestrator caches. It retains the small pinned kernel,
Docker build caches, unrelated Docker images, and external guest files. It does not
run a global Docker prune. Images/disks removed from the cache are recreated or
copied on demand by a later experiment.

## Updating an existing deployment

Update the checkout on the webserver and workers, then run this from
`orchestrator/` on the webserver:

```bash
docker compose --env-file .env up -d --build
```

Use **Redeploy supervisor** on each machine so it also receives the recovery and
local-build changes. Running sibling worker containers keep their frozen inputs.
Do not delete the controller volume or remote `.chimaera-orchestrator` directories
as an upgrade step.

Old failed cases stuck in `collecting` are recovered to a failed execution state
when the controller starts. Use **Retry collection** for download failures, or
**Delete experiment** to cancel collection and abandon the run. If workers are
still active, choose **Stop** first. **Clean remote copies** remains available
after deletion and can explicitly discard uncollected output. New experiments
use the local image/guest cache policy; legacy generated guest caches are left
untouched, while new guests use recipe-specific cache names.

For automatic SDK sharing and calibration-machine selection, update all checkouts,
rebuild the webserver stack, and redeploy supervisors before queueing a new
experiment. Choose the intended producer in **Image preparation & calibration machine**. Existing
frozen experiments retain their original source identities and build keys; queue a
new experiment when adopting the generated-file exclusions, rather than editing
saved hashes. New experiments share both Docker images; older frozen calibrations without an
application image ID keep their original local application build behavior.
SDKs and application images remain cached; the application cache now
includes the SDK image identity so an application built against a different SDK
is rebuilt.

For the guest-init symlink fix, stop the affected experiment, update all checkouts,
rebuild the webserver stack and redeploy supervisors, then queue a new experiment.
Preparation automatically builds a fresh guest disk under the changed recipe key;
the SDK/application images remain cached. Existing prepared experiments still
reference their original disk, so retrying only their failed cases reuses that
disk. If a machine has an explicitly configured guest disk path, clear it to use
the corrected generated guest, or supply a separately corrected disk.

## Development and checks

The Python service uses only the standard library. Local dashboard development
needs Node 20.9+ (the Docker build uses Node 22):

```bash
# Terminal 1, repository root
CHIMAERA_REPO="$PWD" CHIMAERA_DATA=/tmp/chimaera-orchestrator \
  python3 orchestrator/backend/server.py

# Terminal 2
cd orchestrator/web
npm ci
npm run dev
```

```bash
# Repository root; no Docker or simulations required
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  orchestrator/tests containers/tests containers/ball-catching/tests

cd orchestrator/web
npm run typecheck
npm run build
npm audit --omit=dev
```

The tests exercise shared calibration validation, dynamic allocation, lost replies,
restart reconciliation, manual retries, queue blocking, stop generations, resource
changes, safe result extraction, and portable report merging. They do not qualify
full-system robot execution on the actual cluster. Before a large run, validate
preparation and one real case on the destination machines.

An opt-in lifecycle check runs synthetic Python cases in real detached Docker
containers and cleans up its own containers afterward. It needs Docker access,
but no ROS, KVM, benchmark image, or guest disk:

```bash
# Repository root
docker build -f orchestrator/backend/Dockerfile \
  -t chimaera-supervisor:orchestrator-check .
mkdir -p /tmp/chimaera-lifecycle
docker run --rm \
  --mount type=bind,source="$PWD",target=/repo,readonly \
  --mount type=bind,source=/tmp/chimaera-lifecycle,target=/tmp/chimaera-lifecycle \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  chimaera-supervisor:orchestrator-check \
  python3 /repo/orchestrator/tests/docker_lifecycle.py
```
