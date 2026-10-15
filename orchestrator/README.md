# Ball-catching orchestration

A self-hosted Next.js dashboard and persistent scheduler for the current
ball-catching memory-contention benchmark. One experiment runs at a time; cases
are dispatched from a shared queue to available worker slots on selected machines.
Queued experiments wait behind unresolved failures.

## Deploy on the webserver

Requirements:

- Docker Engine with Compose, and an up-to-date checkout of this repository.
- Linux x86-64 workers with Docker Engine/Compose, writable repositories, KVM,
  and permission for the SSH user to use Docker and the guest assets.
- SSH key access from the webserver to each worker. No inbound worker HTTP ports
  are required. The SSH user does not need a host Python installation.
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
Leave these fields empty to prepare them automatically. The first selected machine
produces the frozen calibration reference. Every machine uses its own configured
asset paths or constructs its own guest disk and downloads the pinned kernel.
Guest disks and container images are never distributed through the webserver.

CPU settings use Docker quotas, not exclusive core pinning. You choose worker
counts and resource limits; there is no tuning or automatic overcommit policy.
Changes to worker count/size apply to future assignments. Existing cases retain
their launch limits. Connection/repository/asset paths cannot change during an
active experiment. Build parallelism is separate from case worker limits.

The supervisor mounts its host repository at the **same absolute path** inside
the container. This is necessary because sibling workers' Docker bind mounts are
resolved on the host. It runs as the SSH user's UID/GID, with access to the host
Docker socket and KVM device metadata. Only this trusted supervisor controls Docker;
benchmark cases retain the existing runner's read-only mounts, private network,
private IPC, resource limits, and dropped capabilities.

Remote state lives in `<repository>/.chimaera-orchestrator/`, including the
supervisor's SQLite database, frozen inputs, assets, and attempt directories.
Do not remove it while cases are active or resumable. Re-deploying the supervisor
does not remove running sibling workers.

## Configure and run

Choose **New experiment**. Monaco opens the current
`memory_contention_patterns.json` template with the matching Catppuccin theme.
Import/export JSON, edit it, select machines, then **Validate** to preview the
expanded cases. The server uses the benchmark's real validator; the editor also
provides JSON schema diagnostics. `ramulator_config` is relative to the repository
root and must resolve to a file inside that repository.

**Queue experiment** freezes the configuration and source identity. When its turn
arrives, preparation runs automatically:

1. Validate the machine checkout against the queued revision and source fingerprint.
2. Build missing source-keyed SDK/application images on that machine.
3. On every machine, reuse or construct a local ball-catching Ubuntu/ROS guest disk,
   and reuse a supplied kernel or download and verify the pinned gem5 kernel.
4. Run memory-only calibration and fractional-rate checks, then prepare the guest
   bundle. These preparation steps do not run robot benchmark cases.
5. Freeze one calibration set and send only those small JSON records to the other
   machines. They validate the shared calibration against their local simulator,
   configuration and kernel before becoming eligible for case assignments. Local
   disk hashes are recorded separately; independent ext4 images need not be
   byte-for-byte identical.
6. Dispatch cases dynamically as worker slots become available. A ready machine
   can start cases while other machines are still preparing.

SDK image cache keys depend on the content of simulator, transport, bridge, stack
profile and build inputs. Application image keys additionally depend on the robot
application sources. Repository commits, dashboard changes, experiment parameters,
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
closed and expose the preparation error. No worker silently recalibrates.

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

Case execution and collection are separate states. A failed SSH download or missing
output directory is marked **Result collection failed**, with the error visible in
the dashboard. It is not retried on every poll. **Retry collection** downloads the
existing attempt again without rerunning its simulation. You may delete an idle
experiment even when collection is still in progress or has failed. Deletion
cancels its SSH download and prevents late callbacks from restoring deleted files.

**Stop** kills reachable workers immediately. Stop is pending for unreachable
machines, and the queue remains held until they acknowledge it. An assignment
arriving late is rejected using a persisted stop generation. **Resume** preserves
completed cases and queues the others with fresh attempts. There is no coordinated
gem5/Gazebo/ROS checkpoint system in the existing benchmark, so interrupted cases
restart from their beginning. Old attempts remain in the result package.

## Notifications and results

Set `DISCORD_WEBHOOK_URL` in `.env`, then recreate the controller container. The
URL remains server-side and is never returned to the browser.

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
