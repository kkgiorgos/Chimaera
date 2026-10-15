# Ball-catching memory benchmarks in Docker

This workflow uses the shared [container runner](../README.md) and the existing
Ubuntu 22.04 / ROS Humble guest disk. No new disk is constructed. Each worker
runs gem5, Gazebo and the host bridge together, with private network/IPC and
`/tmp` sockets, read-only inputs and its own writable result directory. Several
cases can run concurrently on a larger machine. The guest disk remains read-only;
gem5 uses a separate copy-on-write overlay in each simulation.

The default [experiment](../../ros/ball_catching_ws/experiments/memory_contention_patterns.json)
contains **10 one-throw cases**:

| Cases | Access pattern | Offered pressure |
| --- | --- | --- |
| `case-000` | No aggressor | 0% |
| `case-001`–`case-003` | Sequential 64-byte transactions (`stream`) | 50%, 80%, 90% |
| `case-004`–`case-006` | 4096-byte stride (`stride`) | 50%, 80%, 90% |
| `case-007`–`case-009` | Seeded random transactions (`random`) | 50%, 80%, 90% |

One shared baseline serves all three patterns. Pressure is relative to each
pattern's separately calibrated sustainable bandwidth, not the interface's
204.8 GB/s theoretical peak. The dashboard also shows accepted aggressor traffic,
physical bus occupancy, admission stalls and average/p95/p99 guest DRAM latency.
The source is synthetic GPU-like traffic; it does not execute a GPU application.
All patterns use 80% reads, a 256 MiB address window, seed 1, at most 512 pending
requests and backpressure that retains work when admission is blocked.
Calibration measures this bounded source and access pattern together: request
issue rate can limit throughput before the DRAM interface reaches its peak.
The current source issues at most one transaction per controller cycle. Pressure
percentages therefore do not claim a particular physical-controller occupancy.

Hardware and robot settings match the qualified local configuration: four
3 GHz Timing cores, fixed caches, Orin-like LPDDR5, a fixed 3 m/s loft, 480×360
stereo at 30 Hz, 30 Hz planning, 250 Hz control, 0.5 ms coupling, a two-camera-period
freshness budget, zero extra ready hold and a 1.5-second task window. Each
simulation still waits for arm/fresh-camera readiness and has a twelve-hour wall
budget. CPU/RAM limits below affect host execution speed; they do not change the
simulated architecture. Results remain coarse binary observations.

## Prepare only

Use Linux x86-64, Docker Buildx, a local Docker daemon, Python 3.10+, and the
existing guest disk/kernel. Host ROS and Gazebo are unnecessary. Full-system runs
need readable/writable `/dev/kvm` and hardware virtualization. Build downloads
dependencies; benchmark workers have no external network access.

From the repository root:

```bash
python3 containers/ball-catching/setup.py \
  --output containers/ball-catching/artifacts/memory-patterns-inputs \
  --build-jobs 4 --cpus 4 --memory 12g
```

This builds the shared SDK if missing, builds the ball-catching application image,
freezes scripts/configuration, runs only the three short memory-only calibrations,
checks fractional 50/80/90% pacing with three additional memory-only stream runs,
and stages one guest bundle. It **does not boot a robot guest or run any benchmark
case**. It produces `runner.json`, `inputs.json`, `plan.json` and `prepared/`.
Preparation uses the same worker permissions and read-only isolation as execution.

`--disk PATH` and `--kernel PATH` override the existing artifacts. `--config PATH`
selects another experiment. `--image TAG` and `--sdk-image TAG` select local image
tags; preparation freezes the resolved application image ID. `--skip-build` uses
an already built/transferred application image. `--rebuild-sdk` rebuilds the
shared stack when simulator/bridge sources change.

For a local SDK build that reuses compatible, already validated Ubuntu 22.04
gem5/libm5/Ramulator binaries instead of recompiling gem5:

```bash
python3 containers/ball-catching/setup.py \
  --output containers/ball-catching/artifacts/memory-patterns-inputs \
  --sdk-image chimaera:ball-catching-sdk --prebuilt-gem5 gem5
```

The optional fast build records artifact hashes and checks that the simulator
loads in the image. The normal source-build path remains the default. Runtime
libraries and the application are built/installed in the image; host ROS overlays
are not mounted into workers.

## Run when ready

Choose concurrency to fit the machine. With the defaults, two workers have an
aggregate ceiling of eight host CPUs and 24 GiB RAM, plus Docker/host overhead.
Use one worker for a sequential run. Inputs and outputs must be separate trees.

```bash
# Validate the exported plan without contacting Docker or starting cases.
python3 containers/ball-catching/run.py \
  --inputs containers/ball-catching/artifacts/memory-patterns-inputs \
  --output ros/ball_catching_ws/results/memory-patterns-docker --dry-run

# Explicitly start the full suite only when the larger system is ready.
python3 containers/ball-catching/run.py \
  --inputs containers/ball-catching/artifacts/memory-patterns-inputs \
  --output ros/ball_catching_ws/results/memory-patterns-docker --workers 2
```

Monitor from another terminal, with no ROS setup:

```bash
python3 containers/ball-catching/watch.py \
  ros/ball_catching_ws/results/memory-patterns-docker
```

The generic runner also prints job totals and each active worker's latest log line.
Full records are under `jobs/case-NNN/suite/`; container stdout is in
`jobs/case-NNN/worker.log`. Catch and miss both complete a job. Infrastructure
failures remain flagged, while independent jobs continue. The shared runner
cleans up active containers on interruption.

Repeat the run command with `--resume` to skip completed jobs and resume/retry
unfinished cases. Keep the original image, input snapshot, disk and kernel
unchanged. Each worker checks the frozen input hashes and the simulator/kernel/
bundle/installed execution provenance. The host runner additionally pins the
image ID. Changing concurrency does not require preparing a new suite; changing
the experiment or worker limits does.

## Dashboard

The wrapper generates `comparison/dashboard.html` after execution, including
partial/failed suites. It contains catch/miss, DRAM mean/p95/p99, application
processing times, image age, delivered control cadence, timing-export coverage,
wall time and motion replay. Missing records remain explicitly flagged.

Refresh the report during or after a run without launching a simulation:

```bash
python3 containers/ball-catching/run.py \
  --inputs containers/ball-catching/artifacts/memory-patterns-inputs \
  --output ros/ball_catching_ws/results/memory-patterns-docker --report-only
```

## Move to another machine

Transfer the repository and the external guest disk/kernel. The snapshot contains
host-specific mount sources, so prepare a new inputs directory on the destination.
The image and in-container paths remain stable. To avoid rebuilding the image:

```bash
docker save chimaera:ball-catching | gzip > /tmp/chimaera-ball-catching.tar.gz
# Transfer that archive, the repository, and the guest disk/kernel to the server.
docker load -i /path/to/chimaera-ball-catching.tar.gz
python3 containers/ball-catching/setup.py --skip-build \
  --disk /path/to/x86-ubuntu-22.04-ros-humble.img \
  --kernel /path/to/x86-linux-kernel-5.15.180 \
  --output /path/to/memory-patterns-inputs --cpus 4 --memory 12g
```

Destination preparation repeats only the short memory calibrations and guest
packaging, not robot trials. Run the wrapper with these new inputs and a fresh
result directory. Starting a new suite provides a matching Docker baseline;
historical native/local guest results remain separate.

## Checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  containers/tests containers/ball-catching/tests
```

Container validation should include image build, input/guest preparation,
read-only mounts, private socket isolation, KVM access and a full-system
instantiation at tick zero. A short native robot smoke test can verify Gazebo and
application dependencies. These checks do not replace the deferred full guest
benchmark. The existing [memory guide](../../docs/ball-catching-memory-contention.md)
documents measurement boundaries and local validation history.

## Per-node ROS DRAM latency

For a fresh run with separate perception, planning, control, and guest-bridge
DRAM read statistics, use the opt-in
[`memory_contention_patterns_node_dram.json`](../../ros/ball_catching_ws/experiments/memory_contention_patterns_node_dram.json)
preset. This preset runs seven cases: baseline plus stream, stride, and random at 80% and 90%. The exact preparation command, attribution boundaries, and legacy
retry-timestamp correction are documented in the
[memory guide](../../docs/ball-catching-memory-contention.md#per-node-dram-reads-and-request-stable-retry-timing-2026-10-15).
Use new inputs/results and the `chimaera:ball-catching-node-dram-sdk` /
`chimaera:ball-catching-node-dram` image tags. The dashboard has a **DRAM reads**
selector and exports all per-node distributions. Completed historical records
lack the attribution needed for this breakdown.
