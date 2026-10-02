# Run wall-follow benchmarks with Docker

Docker supplies Ubuntu 22.04, ROS 2 Humble, Gazebo Fortress, and custom gem5.
You build once, then run benchmarks from the host. Each worker runs Gazebo and
gem5 together; the robot controller runs inside gem5's Linux guest.

## Requirements

- Linux x86-64, Python 3.10+, Git, and a **local** Docker daemon with Buildx.
- NumPy in the host Python environment, for the comparison report. On Ubuntu:
  `sudo apt-get install python3-numpy`.
- Read/write access to Docker and `/dev/kvm` as your ordinary user. Enable
  hardware virtualization; a VM host also needs nested virtualization.
- A readable/writable render device (default `/dev/dri/renderD128`), or use
  `--software-rendering` on the check and run commands below.
- Internet access for the first build and kernel download. Allow space for
  source builds, Docker's cache, images, guest disks, and results. Each running
  worker defaults to 2 CPUs and an 8 GiB memory limit, in addition to host needs.

You do not need host ROS, Gazebo, gem5 builds, guest images, or loop mounts.
Run all commands below **from the repository root**, without `sudo`.

## Build, check, run

```bash
# Build the image, prepare a guest, and select it for future runs.
python3 containers/wall-follow/build.py --jobs 3

# Check image integrity, KVM, isolation, and actual Gazebo lidar/clock delivery.
python3 containers/wall-follow/worker.py check --output results/worker-check

# Run the four cases in demo.json, then generate an offline comparison dashboard.
python3 containers/wall-follow/run-suite.py --output results/demo
```

Open `results/demo/comparison/dashboard.html` when the suite completes.
The report also includes CSV/JSON summaries and coverage of the planned runs.
A nonzero exit means a run failed or the report is incomplete.

The default run uses `ros/wall_follow_ws/experiments/demo.json`, architecture
label `baseline`, one worker, and zero warmup. The five-second demo can take
several minutes **per case** because gem5 simulates CPU execution. `--jobs`
controls build parallelism; `--workers` controls concurrent benchmark containers.

Builds reuse Docker layers and gem5's compilation cache. Guest disks live in
`containers/wall-follow/guest-assets/`. Preparation reuses a matching disk or
creates a new immutable directory, then selects it automatically. Rebuilding
never overwrites a guest used by an existing run. Automatic selection also checks
that the guest belongs to the running image. Older standalone-bridge disks
are rejected; rerun the default build to prepare a compatible guest.

## Configure a benchmark

```bash
python3 containers/wall-follow/run-suite.py \
  --config ros/wall_follow_ws/experiments/demo.json \
  --architecture baseline --workers 2 --cpus 2 --memory 8g \
  --output results/my-benchmark
```

Edit the JSON's `fixed` parameters, `sweep`, and `repetitions`; see the
[benchmark guide](../../ros/wall_follow_ws/README.md) for parameter meanings.
`gui` must be false. `duration` is simulation time; `wall_timeout` is the maximum
real time allowed per case. Increase the latter for slow CPUs or heavy sweeps.
Use fewer workers if CPU or memory contention makes runs too slow.

`--architecture` labels results; CPU/cache settings come from the JSON.
Use `--warmup SECONDS` to exclude initial task measurements, keeping it below
run duration. It does not exclude startup from the separate timing records.
Choose a new output directory for each new benchmark.

Useful options:

| Option | Purpose |
| --- | --- |
| `--dry-run` | Show cases and assignments without Docker or output writes. |
| `--resume` | Retry interrupted/failed cases and retain completed cases. |
| `--workers N` | Run up to N cases concurrently; each gets a fresh container. |
| `--render-device /dev/dri/renderD129` | Select another render node. |
| `--software-rendering` | Use Mesa software rendering without a render device. |
| `--guest-assets DIRECTORY` | Override the automatically selected guest. |
| `--local` | Run the native controller instead of gem5; no KVM or guest needed. |

For a quick native check:

```bash
python3 containers/wall-follow/run-suite.py --local \
  --config containers/wall-follow/experiments/smoke.json --output results/native
```

## Stop, resume, and find failures

Press Ctrl+C to stop a suite. The launcher waits for its containers to be removed.
Repeat the same command with `--resume` to continue. Configuration, worker image,
guest files, and execution settings must match; only `--workers` may change.
After rebuilding or changing inputs, choose a new output directory.

The output contains:

| Path | Contents |
| --- | --- |
| `orchestration.json` | Suite status, inputs, assignments, and exit codes. |
| `jobs/<case>/worker.json` | Exact Docker image ID, guest paths, and launch command. |
| `jobs/<case>/launches/*/worker.log` | Worker console output. |
| `jobs/<case>/suite/runs/<case>/attempt_*/launch.log` | ROS, Gazebo, and gem5 output. |
| `jobs/<case>/suite/runs/<case>/attempt_*/gem5/board.pc.com_1.device` | Guest boot and application output. |
| `comparison/dashboard.html` | Comparison of successful attempts, marked partial if cases failed. |

- **Missing NumPy:** install it in the Python environment running the suite.
- **Device permission error:** grant your user Docker/KVM/render access, then
  log in again if group membership changed. Check with
  `test -r /dev/kvm && test -w /dev/kvm`; use software rendering if needed.
- **Guest startup failure:** inspect the guest serial log above. Confirm
  `worker.json` names the intended guest directory. Run the default build to
  generate/select a current guest; avoid resuming an output with different assets.
- **Exact package version unavailable:** refresh the image and guest with
  `python3 containers/wall-follow/build.py --jobs 3 --no-cache`.
- **Wall timeout:** increase `fixed.wall_timeout` in your config or reduce
  concurrency, then start a new output directory.
- **Existing output / resume mismatch:** use a new output path, or restore the
  original inputs and options before adding `--resume`.

## Development and individual stages

The normal workflow needs only `build.py`, `worker.py check`, and `run-suite.py`.
The lower-level commands remain available for development:

| Command | Purpose |
| --- | --- |
| `build.py --target build-env` | Build only the dependency environment. |
| `build.py --target builder` | Build only the SDK (gem5, libm5, ROS overlay). |
| `build.py --target worker` | Build the shared image without preparing a guest. |
| `build.py --target artifacts --output PATH` | Export compiled SDK files/manifests into an empty directory. |
| `prepare-guest.py` | Prepare/select the guest from the current local SDK. |
| `prepare-guest.py --output PATH` | Prepare a guest at an explicit new path; pass it with `--guest-assets`. |

The default build tags the same image as `chimaera-worker:jammy-humble-fortress`
and `chimaera-builder:jammy-humble-fortress` (the SDK alias for guest preparation).
The image includes build tools so build and runtime dependencies stay identical. `--print-command` previews build commands.
`--profile`, `--tag`, `--sdk-image`, and `--kernel` are advanced overrides;
consult each command's `--help`. After source changes, rerun the default build.

Implementation map:

| File/directory | Responsibility |
| --- | --- |
| `Dockerfile`, `scripts/compile-*` | Install dependencies once, compile the SDK, and add the worker commands. |
| `Guest.Dockerfile`, `prepare-guest.py`, `scripts/*guest*` | Stage the session and construct a bootable partition-2 guest disk. |
| `profiles/` | Pinned base/downloads and build/guest package choices. |
| `guest_assets.py` | Verify assets and remember the selected guest. |
| `worker.py` | Device access, input/output mounts, resource limits, and cleanup. |
| `run-suite.py` | Schedule cases, resume, collect results, and build the report. |
| `suite_adapter.py` | Select cases for the existing benchmark runner; reporting uses its shared comparison command. |

Workers have private network, IPC, temporary files, and ROS/Gazebo state.
Only their output is mounted writable. Guest disks and other inputs are
read-only; gem5 uses an in-memory copy-on-write disk. Launchers pin Docker image
IDs and record input identities. Preserve images, manifests, and results for
reproducibility; pinned base images do not freeze packages in upstream repositories.

Host regression tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q containers/wall-follow/tests
```

`verify-workers.py` tests real container concurrency and cleanup;
`verify-suite.py` tests scheduling, reporting, and resume. Use fresh `--output`
directories. For gem5 verification, give `verify-suite.py --gem5 --guest-assets
DIRECTORY`. These developer checks are separate from ordinary benchmark runs.
