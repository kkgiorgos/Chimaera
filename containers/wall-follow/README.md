# Wall-follow with the shared container image

Develop the application or run benchmark suites using the [shared image and
runner](../README.md). Wall-follow sources, compiled overlays, and guest assets
are prepared separately and mounted into containers.

Run all commands from the repository root.

## Requirements

See the [shared requirements](../README.md#requirements): Linux x86-64, Python
3.10+, a local Docker daemon with Buildx, and sufficient resources. Run as an
ordinary user with Docker access. Setup downloads dependencies; host ROS/Gazebo
installations are unnecessary. Benchmark workers use software rendering.

Gem5 suites need KVM access and virtualization support. Native controller suites
and development need no guest disk or KVM.

## Local application development

```bash
python3 containers/wall-follow/dev.py
python3 containers/wall-follow/dev.py --headless lidar_hz:=10
python3 containers/wall-follow/dev.py --headless --shell
```

`dev.py` builds the image if missing, rebuilds the overlay, and launches Gazebo,
its ROS adapter, and the controller. GUI defaults to on, using host X11/XWayland
`DISPLAY`, its socket, and Xauthority when available. `--headless` needs no display;
`--shell` opens an interactive ROS environment.

The workspace is writable at `/workspace`, and the overlay is read-only at
`/overlay`. Re-run after source changes. Pass [ROS launch arguments](../../ros/wall_follow_ws/README.md#local-application-development)
after command options. Development uses host networking; benchmark workers use
isolated networks and read-only inputs.

## Automated benchmark suites

For a complete gem5 suite:

```bash
python3 containers/wall-follow/suite.py \
  --config ros/wall_follow_ws/experiments/cache_hierarchy.json --output results/cache-hierarchy
```

The command builds the image if missing, builds the overlay, prepares matching
guest assets, exports/runs jobs, and generates `comparison/dashboard.html` under
the output directory. Reporting runs in the image without host analysis dependencies.

For a short native controller suite:

```bash
python3 containers/wall-follow/suite.py --local \
  --config containers/wall-follow/experiments/smoke.json --output results/native
```

| Option | Purpose |
| --- | --- |
| `--config PATH` | Experiment JSON; default `ros/wall_follow_ws/experiments/demo.json` |
| `--output PATH` | Required fresh suite directory, or original directory with `--resume` |
| `--local` | Native controller; skip guest/KVM setup |
| `--workers N` | Concurrent jobs; default 1 |
| `--jobs N` | Build parallelism; default 3 |
| `--image TAG` | Shared image; default `chimaera:jammy-humble-fortress` |
| `--architecture LABEL` | Result label; defaults to `gem5` or `local` |
| `--warmup SECONDS` | Exclude initial robot measurements from report metrics; default 0 |
| `--rebuild-image` | Rebuild the image for a fresh suite |
| `--resume` | Reuse inputs, retry unsuccessful jobs, and regenerate the report |

Jobs default to 2 CPUs and 8 GiB each; choose concurrency to fit host resources.
Experiments require `gui=false`; hardware settings require gem5. `duration` is
simulation time and `wall_timeout` limits real time. A five-second gem5 case can
take several minutes. See [sweep parameters](../../ros/wall_follow_ws/README.md#define-a-sweep).

## Structure

| Component | Responsibility |
| --- | --- |
| [`dev.py`](dev.py) | Application launch or development shell |
| [`suite.py`](suite.py), [`workflow.py`](workflow.py) | Complete suite preparation, execution, and reporting |
| [`build-overlay.py`](build-overlay.py) | Export compiled application packages |
| [`prepare-guest.py`](prepare-guest.py), [`Guest.Dockerfile`](Guest.Dockerfile) | Build a matching guest disk and obtain the pinned kernel |
| [`profiles/`](profiles/), [`scripts/`](scripts/), [`guest_assets.py`](guest_assets.py) | Guest policy, construction, boot setup, and asset verification |
| [`configure.py`](configure.py) | Export sweeps/repetitions as generic runner jobs |
| [`compare.py`](compare.py) | Report eligible completed attempts |
| [`tests/`](tests/) | Setup and workflow checks |

## Individual setup steps

Use these steps to control preparation or edit worker settings before execution:

```bash
python3 containers/build.py --jobs 3
python3 containers/wall-follow/build-overlay.py --jobs 3
python3 containers/wall-follow/prepare-guest.py
python3 containers/wall-follow/configure.py \
  --config ros/wall_follow_ws/experiments/demo.json \
  --output containers/wall-follow/run-configs/demo
python3 containers/run.py \
  --config containers/wall-follow/run-configs/demo/runner.json \
  --output results/demo --workers 2
```

The default overlay is `containers/wall-follow/overlay/`; the universal bridge is
already in the image. Guest preparation combines both in a temporary construction
stage, then removes construction images/tags.

`configure.py` snapshots the experiment, expands jobs, and verifies the selected
guest once. Override `--image`, `--overlay`, or `--guest-assets` for alternate inputs;
`--architecture` labels results. Edit exported `runner.json` for resource limits,
mounts, environment, or command arguments. `CHIMAERA_SETUP` sources the overlay.

For native execution, omit guest preparation and add `--local` to `configure.py`,
using an experiment without hardware settings, such as
[`experiments/smoke.json`](experiments/smoke.json). Then run the exported JSON normally.

## Resume a suite

Repeat the original `suite.py` command with `--resume`. Preserve the experiment,
image, mode, architecture label, and mounted inputs. Resume reuses the plan and
overlay without rebuilding; it cannot be combined with `--rebuild-image`.
Concurrency and report warmup may change. Use fresh output after changing inputs.

For manual runs, use the [generic runner's `--resume`](../README.md#resume-a-run).
The exported benchmark command also resumes, retaining successful attempts.

## Read the results

See the [example dashboard](../../docs/examples/wall-follow-dashboard.html) for
a report covering 62 of 64 planned demo runs. Download the HTML file and open it
in a browser to explore it offline.

| Path | Contents |
| --- | --- |
| `<output>-inputs/overlay/` | Built application packages |
| `<output>-inputs/plan/` | `experiment.json` snapshot and `runner.json` |
| `<output>/run.json` | Worker commands, exit codes, and statuses |
| `<output>/jobs/<case>/worker.log` | Container output |
| `<output>/jobs/<case>/suite/runs/<case>/attempt_*/` | Benchmark records; `launch.log` has ROS/Gazebo/gem5 output |
| `<attempt>/gem5/board.pc.com_1.device` | Guest serial output |
| `<output>/comparison/` | Offline `dashboard.html`, summaries, and `coverage.json` |

`suite.py` attempts reporting even when jobs fail, returning nonzero if execution
or reporting fails. Reports select the latest eligible attempt from each completed
job and mark partial coverage; at least one eligible completed result is required.

For manual runs, install the host analysis library and generate the report:

```bash
python3 -m pip install -e ros/wall_follow_ws/benchmarking
python3 containers/wall-follow/compare.py --output results/demo \
  --report results/demo/comparison
```

Open `results/demo/comparison/dashboard.html`. `--warmup SECONDS` excludes initial
robot measurements. See the [benchmark results guide](../../ros/wall_follow_ws/README.md#read-the-results)
for metrics; keep raw attempt files when copying results.

## Custom setup and guest assets

Change shared dependencies in [`containers/profiles/`](../profiles/) or derive an
image with additional libraries. Application-only changes require rebuilding the
overlay and matching guest, rather than the shared image. Other applications can
export an overlay and mount it with `CHIMAERA_SETUP`.

`prepare-guest.py` accepts `--sdk-image IMAGE`, `--overlay PATH`, `--output PATH`,
and `--kernel PATH` for a local copy of the pinned kernel. By default, assets live
in `containers/wall-follow/guest-assets/jammy-humble-fortress-<input-hash>/`.
Identical inputs reuse verified assets; changed inputs produce a new directory.
The profile's `.current.json` selection updates only after verification. Explicit
`--output` leaves selection unchanged; pass that directory to `configure.py`
with `--guest-assets`.

Assets include `disk.img`, `kernel`, `manifest.json`, and `payload.json`. Guest
policy specifies a partition-2 root disk and runtime packages. Other benchmarks
may supply their own disk/kernel directly to the generic runner.

See [image/cache cleanup](../README.md#keep-disk-usage-under-control). Keep guest
assets and images needed by active or resumable runs; remove unused guest directories.

## Checks

Run [container host tests](../README.md#checks) without Docker execution. For an
end-to-end native check, use the smoke suite above with fresh output. Application
and benchmark tests are in the [workspace guide](../../ros/wall_follow_ws/README.md#checks).
