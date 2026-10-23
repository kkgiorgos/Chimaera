# Ball-catching orchestration

A self-hosted Next.js dashboard and persistent scheduler for the current
ball-catching memory-contention benchmark. One experiment runs at a time; cases
are dispatched from a shared queue to available worker slots on selected machines.
Only one unfinished experiment can exist, including stopped runs. Finalized runs
retain collected results; their working files are released automatically before
another experiment can start.

New experiments default to direct **DDS + m5ops**, using
[`experiments/dds_m5ops_two_lofts.json`](experiments/dds_m5ops_two_lofts.json).
It runs the saved 3 and 5 m/s lofts once each without memory pressure. Existing
frozen experiments keep their selected implementation.

```json
"transport_backend": "m5ops",
"middleware_transport": "fastdds",
"m5_module": ""
```

Leave `fixed.m5_module` and the preparation machine's **kernel** field empty.
The producer builds Linux 5.15.180 and `chimaera_m5.ko` from the same configured
kernel tree, with module version checks enabled. The shared SDK includes the
pinned custom Fast DDS RMW and direct transport plugin. Each case boots with KVM
and switches to the CPU selected in `hardware`; readiness checkpoints are
unavailable with m5ops. Guest receive notification uses the driver and interrupts.
Payload copying is functional rather than timed DMA; CPU/application and memory
work still run on the selected simulated hardware.

The Linux source archive is pinned by SHA-256 in `kernel/policy.json`;
`kernel/config.fragment` sets the built-in disk/filesystem, serial console and
module support. Initial preparation needs Internet access, several GiB of
temporary disk space, and a full kernel/simulator build. Matching cached build
inputs are reused. Backend, driver, ABI and recipe changes invalidate the matching
kernel/module cache. Compiler, configuration, release, vermagic and file hashes
are retained.

Before preparation is marked ready, a disposable COW guest boots the actual
kernel/disk/module, opens `/dev/chimaera-m5`, and checks ABI 1. Failure prevents
case dispatch. This preparation probe does not replace an actual robot trial.
Other machines receive the producer's exact verified kernel/module pair, rather
than compiling independently. The current pair and those used by unfinished
experiments remain protected from cleanup.

For a custom kernel, supply both the preparation machine's kernel path and a
repository-relative `fixed.m5_module` built for that exact kernel. Explicit pairs
also pass the boot probe. Standalone container configs resolve module paths
relative to their JSON file; the orchestrator's editor uses repository-relative
paths. Keep the kernel field empty to use automatic pair preparation.

The older `dma` backend remains available with `fixed.dma_module`, its matching
kernel pair and `/dev/chimaera-dma` ABI probe. It also uses fresh boots. The legacy
`m5` backend retains its pinned prebuilt kernel and readiness checkpoint workflow.

## Deploy on the webserver

Requirements:

- Docker Engine with Compose, and an up-to-date checkout of this repository.
- Linux x86-64 workers with Docker Engine/Compose, writable repositories,
  and permission for the SSH user to use Docker and the guest assets.
- With the default m5 backend, KVM on the selected preparation machine for its
  initial guest boot. Case workers restore detailed checkpoints. With DMA or m5ops, every
  case worker needs `/dev/kvm` for a fresh guest boot.
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
That machine maintains three replaceable image slots: `chimaera:orchestrator-sdk`
(simulator and bridge), `chimaera:orchestrator-base` (installed application
dependencies), and `chimaera:orchestrator-app` (the installed ROS application).
The slots share Docker layers. Application packages compile in a disposable
container built from the installed base; only its installed overlay is committed
to the application image, with build trees and source mounts excluded. This
avoids a second BuildKit cache of application revisions. Application source
changes replace only the application; dependency/simulator changes rebuild the affected base. Build sources
and intermediate compilation files stay out of the final images.

Only the application image is streamed to consumers; it already includes the SDK
and dependency layers. Images remain pinned by immutable IDs during a run. The
producer separately streams the exact backing disk, kernel, bundle and readiness
checkpoints as a compressed sparse tar stream. No disk is added to an image, no
transfer archive is written to disk, and producer export links reference the
original files without copying them. Consumers extract directly into an incoming
directory, verify every asset/checkpoint hash, and publish it atomically. Consumer
disk/kernel overrides are unused. The controller receives only progress and
results; producer credentials and SSH configuration remain on its host.

The producer builds its reusable guest from the dependency base, so changing only
ROS application code does not construct another full guest disk. The guest bundle
injects that experiment's application at boot. Consumers retain one sparse disk
copy for the active experiment and mount it read-only across their case workers.

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

Generated transport kernel build outputs (`*.o`, `*.ko`, `*.mod`, `*.mod.c`,
`.*.cmd`, `Module.symvers`, `modules.order`, and `.tmp_versions/`) are excluded
from the SDK build context and build fingerprints. Building a driver on the
preparation machine therefore does not make a consumer with the same sources
appear different. Driver sources, headers, and the Makefile still participate in
the fingerprint. Experiments queued before this exclusion retain their original
hashes; update the controller and supervisors on all machines and queue a new
experiment with fresh preparation/calibration rather than retrying the old one.

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

Supervisor deployment records the host KVM group as `CHIMAERA_KVM_GID`, so fresh-boot
case workers receive the right supplementary group even though `/dev/kvm` is absent
from the supervisor itself. For manual Compose deployment, set this variable to
`stat -c %g /dev/kvm` on the worker host. Redeploy supervisors after updating this
setting; existing dispatch reservations retry with their original attempt IDs.

Remote state lives in `<repository>/.chimaera-orchestrator/`, including the
supervisor's SQLite database, frozen inputs, assets, and attempt directories.
Do not remove it while cases are active or resumable. Re-deploying the supervisor
does not remove running sibling workers.

## Configure and run

For the two saved lofts, use the default New experiment configuration or import
[`experiments/dds_m5ops_two_lofts.json`](experiments/dds_m5ops_two_lofts.json).
**Validate** must show exactly two cases: `loft-3` and `loft-5`, both `camera_30`
and zero pressure. See [the run settings](experiments/dds_m5ops_two_lofts.md).
Select one worker slot with 4 host CPUs and a 12 GiB RAM budget on the preparation
machine for a serial first run. Leave disk/kernel overrides empty for automatic
assets, and queue a new experiment after updating the controller and supervisors.
No simulation starts merely by importing or validating this config.

For the older planner timing correction, import
[`experiments/planner_timing_baseline.json`](experiments/planner_timing_baseline.json)
to run a single unloaded 3 m/s case. Follow the
[build and checkpoint instructions](../docs/ball-catching-planner-timing.md#test-through-the-orchestrator)
before queueing it.

Choose **New experiment**. Monaco opens the current
two-loft DDS + m5ops template with the matching Catppuccin theme.
Import/export JSON, edit it, select workers and an image preparation/calibration
machine, then **Validate** to preview the expanded cases. The server uses the benchmark's real validator; the editor also
provides JSON schema diagnostics. `ramulator_config` is relative to the repository
root and must resolve to a file inside that repository.

**Start experiment** freezes the configuration and source identity. When its turn
arrives, preparation runs automatically:

1. Validate the frozen source identity and build or reuse the current SDK,
   dependency base and application. A dedicated Buildx builder named
   `chimaera-orchestrator` uses a 2 GB retention target for reclaimable cache;
   no other builder or daemon-wide cache is pruned.
2. Reuse or construct the producer's base guest and kernel. Calibrate and freeze
   the application bundle. With DMA/m5ops, build and boot-check the exact matching
   driver pair; every case subsequently boots with KVM. With legacy `m5`, create
   readiness checkpoints once per compatible configuration on the producer.
3. Stream the application image and separate sparse readiness assets directly to
   each consumer over SSH. Consumers do not build a second SDK, disk or checkpoint.
4. Verify image content and all asset hashes, validate shared calibration, and
   freeze each machine's runner with read-only disk and checkpoint mounts.
5. Dispatch cases to ready workers. Once an attempt is verified and committed on
   the controller, remove its remote output. Failed downloads preserve their
   worker copy until **Retry collection** succeeds.
6. After completion, or **Finalize results** on a stopped/failed run, collect all
   remaining attempts and preparation diagnostics, remove experiment files and
   obsolete image/disk versions on every selected worker, and retain central
   results/configuration/provenance. Cleanup retries after disconnects/restarts;
   a new experiment waits for acknowledgement from every involved machine.

The legacy `m5` checkpoint workflow restores the same compatible, aggressor-free
detailed guest state for every case.
While gem5 stays paused, its host replays the recorded readiness commands at native
speed to reconstruct Gazebo time, robot motion and readiness. A mismatch in saved
time or joint positions/velocities fails the case. This is a host input replay,
not a serialized snapshot of Gazebo's renderer or physics engine internals.
The restored guest reconnects to a fresh bridge. Drained dirty cache data is
preserved in RAM; MESI caches restart empty to avoid its unsupported flush and
warm-cache reconstruction paths. Before enabling pressure, the host advances
through the time consumed by guest checkpoint drain so both clocks catch up.
Case pressure then runs for
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

Fresh-boot workers (including DMA and m5ops) also receive the dependency base image used
to construct their guest disk. Workers using transferred readiness checkpoints
or an explicitly configured disk do not need that extra image. Destination image
identities are recorded separately from the producer's calibration provenance.

The dashboard shows **Copying prepared images** during transfer, with the current
application or guest base image, compressed bytes sent, average transfer rate, elapsed time,
and an estimated ETA for the current image based on its uncompressed Docker size
and measured export/network throughput. The ETA excludes loading and verification;
those stages display their status instead. ETA appears after throughput is sampled.
Readiness transfer has a separate sparse-asset stage without an estimated percentage.
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
recorded speed. The adjacent **Simulated time** column shows simulated seconds
reached at that sample to four decimal places, with the same pending and stale
sample labels.

Preparation also shows its current calibration, bundle, or checkpoint stage and
total elapsed wall time. During readiness it reports the current checkpoint out of
the total, simulated time, completed exchanges, simulation speed, arm readiness,
fresh camera readiness, and extra warmup when configured. After readiness it shows
the guest barrier and memory/device serialization stages. Stale timing samples
remain labeled **last sample** while checkpoint saving proceeds; readiness has no
predictable percentage or ETA. Failed preparations display their bridge/guest
error, and collected preparation reports include checkpoint launch logs, guest
serial, timing, and readiness records without copying checkpoint RAM.

The guest polls the readfile restore marker while gem5 drains its checkpoint and
only starts a new handshake after the restoring simulator releases it. The gate
does not issue address-mode CPU quiesce operations, which can corrupt outstanding
TimingSimpleCPU instruction-fetch scheduling. A timing socket
timeout during capture is a preparation failure; increasing `startup_timeout_s`
does not fix a transport barrier deadlock. After updating this protocol, align
controller and worker sources, rebuild the controller, redeploy supervisors, and
queue a new experiment so its guest bundle and images contain the fix. Retrying
an old experiment retains its frozen source identity.

Checkpoint protocol changes must first pass the bounded full-system regression:

```bash
python3 containers/ball-catching/checkpoint-smoke.py \
  --skip-packaging \
  --image sha256:<built-application-image-id> \
  --disk /absolute/path/to/guest.img --kernel /absolute/path/to/kernel \
  --output /absolute/path/to/new-smoke-directory
```

Run this on a KVM-capable x86 worker with Docker and the existing application
image. It builds the current bridges in an isolated overlay, uses a small ROS
readiness application, saves the real Linux guest, restores twice without
`/dev/kvm`, and collects the preparation diagnostics. Each restore checks host state replay,
pressure and launch barriers, physical launch validation, and a fresh response
from preserved guest application state. Each simulation has a 180-second wall
watchdog and a 30 ms detailed-time budget. Other phases have five-minute
watchdogs; optional full-disk image packaging has ten minutes. Logs and
`smoke-result.json` remain in the output directory. Containers
are disposable; test images and imported assets remain available for inspection.
Omit `--skip-packaging` to also exercise sparse readiness export/import and
changed-index rejection. Both restore paths mount checkpoints outside the image;
the original backing disk remains unchanged.

Build reuse checks both source keys and the actual image ID in each stable slot.
Host editor caches, generated parser tables and simulation outputs are excluded
from keys. A new Git commit, dashboard change, experiment setting or worker size
does not by itself rebuild the simulator. Replacing an application always starts
from the dependency base, so image layers do not accumulate successive overlays.
Old versions become eligible for scoped cleanup when no unfinished experiment or
container references them. One current producer guest/kernel and the current
image slots remain reusable; no version history is retained in these caches.

Calibration measures the **simulated** memory system's sustainable bandwidth for
each pattern. A 50% pressure case requests half that pattern's calibrated rate;
it is unrelated to the physical worker's RAM bandwidth. Reuse requires matching
simulator/configuration hashes. Independent simulator builds use a fixed compiler
date via `SOURCE_DATE_EPOCH`; genuinely different binaries/dependencies still fail
closed and expose the preparation error. The fixed date does not pin packages
installed from live APT repositories: separately built SDKs can have different
dependencies and simulator binaries even when their source keys match. To reuse a
calibration reliably, the orchestrator copies the producer application
image, including its SDK layers, automatically using immutable identities. A missing copied
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

The generated guest uses the dependency base's Ubuntu/ROS filesystem, a generic
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
queue until you **Retry failed cases** or **Finalize results**. Preparation
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

Completed experiments keep only central results and small history records.
Stopped and failed experiments keep their shared inputs for recovery until
**Finalize results** is selected. Finalization is irreversible: the configuration
can be cloned for another run, but the finalized checkpoint is not retained.
Uncollected output blocks release rather than silently losing results. The
explicit **Clean remote copies** action can discard uncollected output when that
is intentional. **Delete experiment** deletes central results, retains a deleted
history entry and automatically releases remote working files after outstanding
operations settle.

## Reclaim unused machine assets

Automatic preparation/finalization cleanup removes obsolete managed image and
generated disk versions. The current reusable image slots, one producer guest,
pinned kernel, unfinished experiment dependencies, containers and explicitly
configured external assets are protected. **Clean unused assets** remains useful
for legacy caches. Cleanup never runs a global Docker image/volume prune; only
the dedicated orchestrator builder's cache has the 2 GB retention target.

## Updating an existing deployment

Finish or stop existing experiments before adopting this storage layout. Update
all worker and webserver checkouts, rebuild the controller/dashboard with
`docker compose --env-file .env up -d --build` from this directory, and redeploy
supervisors. The first new preparation builds the new image stages; existing
checkpoint images are not compatible with the separate-asset transfer protocol.

Finalize old stopped/failed runs after collecting their results. Old completed
runs are released automatically after every recorded result is collected. If
there are several old queued runs, stop and finalize unwanted ones before
starting a new experiment. Legacy caches referenced by unfinished runs remain
protected. Do not delete the controller volume or edit saved provenance hashes.
Manual checkpoint smoke directories and unrelated Docker images remain outside
automatic cleanup.

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

## Reviewing and reclaiming storage

Open **Storage** in the dashboard sidebar and choose the dashboard server or a
worker machine. **Refresh usage** scans on demand and shows filesystem free space,
experiment file sizes, and managed Docker image and guest disk caches on workers.
Guest disks are sparse: the displayed size is allocated disk space rather than
their virtual capacity. Docker image sizes include shared layers, so removing an
image may reclaim less than its displayed size.

Select unused cached assets and choose **Remove selected**. Saved experiment
dependencies, configured guest disks and kernels, and container dependencies are
protected. Removal checks dependencies again and refuses stale or protected
selections. Stop experiments on that machine before removing caches.

To explicitly discard files retained by an old experiment, use **Remove remote
copies** for that experiment on the selected machine. This discards remote inputs,
checkpoints, logs and uncollected output while retaining collected dashboard
results. Refresh usage afterwards, then remove caches that are now unused.
**Delete files** on the dashboard server removes collected results and staging
files while keeping experiment history. Download any results to keep first.

Rebuild/restart the dashboard services and redeploy existing machine supervisors
to enable the new storage inspection RPC. Storage inspection itself is read-only;
it does not prune Docker or remove files. Files outside managed experiments and
caches (for example manual checkpoint smoke-test directories) remain outside the
removal controls.

The application image builder also has an opt-in Docker smoke check using tiny
synthetic ROS tools, with no gem5/Gazebo simulation. It checks replacement from
the same base, runtime entrypoint/environment, and exclusion of source mounts,
then removes its test containers and images:

```bash
python3 orchestrator/tests/docker_images.py
```

### Remove a machine

Choose **Remove machine** on its machine card. With no cleanup selected, this
only unregisters the machine and requires no SSH access. The supervisor and all
remote resources remain in place.

Optional cleanup removes the supervisor container and lets you select remote
experiment files (including stopped experiment containers), Chimaera Docker
images, and generated guest disks and kernel/module pairs. Selecting any resource
cleanup also selects supervisor removal. Type the machine name to confirm.
Collected results and experiment history stored by the controller remain available,
including collected logs for machines that have been removed.

Finalize experiments and wait for collection and remote release before removing
an involved machine. Cleanup uses SSH and host Python 3, so it works even without
a running supervisor. Running Chimaera workers block remote cleanup. Configured
guest assets, mounted files, the repository, and unrelated Docker images are kept;
Docker images are never force-removed and no host-wide Docker prune is used.
Small supervisor release markers remain to prevent old work from restarting.
If cleanup fails, the machine stays registered with an error. Completed cleanup
steps cannot be undone; retry removal after resolving the error, or unregister
without further cleanup. Removal resumes after a controller restart.

A machine may also host the orchestrator. Cleanup preserves its controller,
dashboard, and controller results volume. The controller's read-only source mount
and read-only host filesystem views (such as disk usage browsers) do not pin
remote working files; direct asset mounts and writable mounts remain protected.
Removal supports both Docker and Podman's Docker-compatible CLI.
