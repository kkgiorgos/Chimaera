# Wall-follow with the shared container image

The [shared image and runner](../README.md) are independent of benchmarks.
This directory builds a wall-follow ROS overlay on the host through Docker,
prepares the application's guest disk, and exports jobs. Workers mount the
benchmark workspace, compiled overlay, experiment files, disk and kernel read-only.
No wall-follow files are included in the shared image.

## Automatic workflows

Run from the repository root. A complete suite uses one command:

```bash
python3 containers/wall-follow/suite.py \
  --config ros/wall_follow_ws/experiments/cache_hierarchy.json --output results/cache-hierarchy
```

The command builds the shared image if missing, builds an isolated application
overlay, prepares matching guest assets, exports the jobs, runs them, and generates
`results/cache-hierarchy/comparison/dashboard.html`. Reporting runs in the image,
so the host needs only standard-library Python and Docker. Inputs live in the sibling
`results/cache-hierarchy-inputs/` directory. `--workers` controls job concurrency;
`--jobs` controls compilation. Use `--resume` with the original arguments to retry
failed jobs and rebuild the report without changing the image or inputs.
`--rebuild-image` refreshes the shared image for a new suite. First-time guest
preparation may download runtime packages and the kernel.

For native controller benchmarking, no guest or KVM is needed:

```bash
python3 containers/wall-follow/suite.py --local \
  --config containers/wall-follow/experiments/smoke.json --output results/native
```

For application development, build and start the local application:

```bash
python3 containers/wall-follow/dev.py
python3 containers/wall-follow/dev.py --headless lidar_hz:=10
python3 containers/wall-follow/dev.py --headless --shell
```

Development runs only Gazebo, its ROS adapter, and the controller. GUI defaults to
on and uses the host X11/XWayland `DISPLAY`, socket, and Xauthority file when
available; `--headless` needs no display. The workspace is mounted writable at
`/workspace`; the compiled overlay is mounted at `/overlay`. Re-run the command
after source changes to rebuild it. `--shell` opens an interactive ROS environment
instead of launching the application. Benchmark worker isolation remains separate
from this development container.

## Individual setup steps

Requirements: Linux x86-64, Python 3.10+, a local Docker daemon with Buildx,
sufficient RAM and disk space, and read/write `/dev/kvm` for gem5 suites. Gem5
also needs hardware virtualization (nested virtualization on a VM). Setup downloads dependencies;
workers use software rendering and need no host ROS/Gazebo installation.
Run from the repository root as an ordinary user with Docker/device access.

```bash
# Shared image: dependencies, custom gem5/libm5 and the universal ROS bridge.
python3 containers/build.py --jobs 3

# Benchmark-specific outputs live outside the image.
python3 containers/wall-follow/build-overlay.py --jobs 3
python3 containers/wall-follow/prepare-guest.py
python3 containers/wall-follow/configure.py \
  --config ros/wall_follow_ws/experiments/demo.json \
  --output containers/wall-follow/run-configs/demo

python3 containers/run.py \
  --config containers/wall-follow/run-configs/demo/runner.json \
  --output results/demo --workers 2
```

`build-overlay.py` compiles only wall-follow packages using the shared image,
exporting to `overlay/`. The universal bridge is already installed in the image.
Guest preparation merges the bridge and application overlay only in its temporary
**guest construction** stage, then deletes construction images and temporary tags.
The resulting guest includes the matching controller and bridge dependencies.

`configure.py` expands sweep/repetitions into individual jobs and snapshots the
experiment. Each job selects a run ID using the shared benchmark runner. It
verifies the selected guest once during setup.
Override `--image`, `--overlay`, or `--guest-assets` for alternate inputs. The
exported JSON is editable: choose CPU/memory limits, rendering, mounts, environment
and common arguments before running. `CHIMAERA_SETUP` sources the mounted
application overlay after the image's ROS and bridge setup.

`--architecture` labels results; CPU/cache settings come from the experiment.
See the [benchmark guide](../../ros/wall_follow_ws/README.md) for parameters.
`gui` must be false. `duration` is simulation time; `wall_timeout` bounds real
execution time. A five-second gem5 case can take several minutes.

For a native controller run, omit guest preparation and export with `--local`:

```bash
python3 containers/wall-follow/configure.py --local \
  --config containers/wall-follow/experiments/smoke.json \
  --output containers/wall-follow/run-configs/native
python3 containers/run.py \
  --config containers/wall-follow/run-configs/native/runner.json \
  --output results/native --workers 2
```

Use the runner's `--resume` to retry unsuccessful jobs. The benchmark command
also uses its own `--resume` to retain successful attempts. Keep source, overlay,
experiment, and guest inputs unchanged during execution and resume. Choose new
configuration/result directories after changing inputs.

## Results

Console output is `jobs/<case>/worker.log`. Benchmark records are below
`jobs/<case>/suite/runs/<case>/attempt_*`; `launch.log` contains ROS/Gazebo/gem5
output, and `gem5/board.pc.com_1.device` contains guest serial output.

Reporting is a separate host step, requiring the benchmark analysis dependencies:

```bash
python3 containers/wall-follow/compare.py --output results/demo \
  --report results/demo/comparison
```

Open `results/demo/comparison/dashboard.html`. Reports include eligible results
from completed jobs and mark partial coverage. Failed jobs remain in `run.json`.
Use `--warmup SECONDS` to exclude initial measurements from analysis.

## Custom setup

The common dependencies are in `containers/profiles/`; add application system
libraries there or build a derived shared image with additional dependencies.
Adding or changing benchmark files alone never requires rebuilding the image.
For another ROS application, build its sources using the shared image and export
a merged overlay, then mount it and set `CHIMAERA_SETUP` in the runner JSON.

`prepare-guest.py --sdk-image IMAGE --overlay PATH --output PATH` builds an
explicit wall-follow guest. `--kernel PATH` supplies the pinned kernel locally.
The guest scripts and `profiles/*.guest.json` describe the partition-2 disk and
runtime package requirements. Other benchmarks can prepare their own disk/kernel
and supply them directly in their generic runner configuration.

See [cleanup](../README.md#keep-disk-usage-under-control) for images and cache.
Only the current shared image, application overlay, selected guest and configured
runs are needed. Old guest directories can be removed after their runs finish.
