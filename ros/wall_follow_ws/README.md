# Wall-follow application and benchmark

This workspace supports two workflows: developing the ROS application locally,
and running complete benchmark suites with either a native controller or gem5.
The C++ controller reads lidar and publishes velocity commands. Gazebo supplies
physics and sensors. A passive host collector records commands, poses, and time;
it never forwards commands or controls simulation. In gem5 runs, Chimaera routes
host `/robot/scan` to guest `/robot/scan`, and guest `/robot/cmd_vel` directly to
host `/cmd_vel`, which the Gazebo adapter consumes.

## Local application development

For the native workflow, install ROS 2 Humble, Gazebo Fortress, `ros_gz_sim`, and
`ros_gz_bridge`. Build only the application from `ros/wall_follow_ws`:

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select wall_follow_robot --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
ros2 launch wall_follow_robot application.launch.py
```

That one launch starts Gazebo, its ROS adapter, and the controller. GUI is enabled
by default; use `gui:=false` for headless execution. It generates a temporary world
from the shared application generator. Adjust `lidar_hz`, `lidar_samples`,
`noise_std`, `physics_step`, `arena_width`, or `arena_height` through launch arguments.
Optional `world:=/absolute/path/world.sdf` and `parameters_file:=/absolute/path/controller.yaml`
let you supply custom development inputs. Without a parameter file, the controller
uses its C++ defaults. There is no collector, results directory, gem5, or Chimaera
process in this workflow. Use `.zsh` setup files when sourcing from zsh.

For container development, run from the repository root:

```bash
python3 containers/wall-follow/dev.py
# Or: python3 containers/wall-follow/dev.py --headless
```

Container setup, GUI/display access, and an interactive development shell are
covered in the [container guide](../../containers/wall-follow/README.md).

## Automated benchmark suites

For container execution, one command from the repository root prepares the image,
builds the application, prepares the guest when needed, runs the jobs, and generates
the report:

```bash
python3 containers/wall-follow/suite.py \
  --config ros/wall_follow_ws/experiments/cache_hierarchy.json --output results/cache-hierarchy
# Add --local for a native controller suite, using a configuration without hardware settings.
```

All container orchestration lives under `containers/`. The benchmark runner itself
has no Docker dependency.

For a native local suite, build all application/benchmark packages and install the
analysis library from `ros/wall_follow_ws`:

```bash
source /opt/ros/humble/setup.bash
colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
python3 -m pip install -e benchmarking
python3 scripts/run_experiments.py --config experiments/demo.json --output results/local
```

The runner starts and stops every runtime component automatically. No separate
host, controller, or bridge startup is required.

For native Chimaera suites, build the shared bridge and application from the
repository root, then deploy once into an offline ROS Humble guest image:

```bash
source /opt/ros/humble/setup.bash
colcon --log-base ros/chimaera_ros_ws/log build \
  --base-paths ros/chimaera_ros_ws/src ros/wall_follow_ws/src \
  --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install \
  --cmake-args -DBUILD_GAZEBO_BRIDGE=ON -DCMAKE_BUILD_TYPE=Release
source ros/chimaera_ros_ws/install/setup.bash
cd ros/wall_follow_ws
python3 -m pip install -e benchmarking
./deploy_guest.sh /absolute/path/to/disk.img 2
python3 scripts/run_experiments.py --gem5 \
  --config experiments/cache_hierarchy.json --output results/cache-hierarchy \
  --image /absolute/path/to/disk.img
```

Chimaera needs a built x86 gem5, KVM access, and a matching kernel/guest image.
See the [bridge guide](../chimaera_ros_ws/README.md) for transport dependencies and
image preparation. Deployment requires sudo; build guest binaries in an environment
matching the image and redeploy after changing guest code. Experiment settings are
injected at boot without redeploying.

## Structure

| Component | Responsibility |
| --- | --- |
| `wall_follow_robot` | Controller, shared world generator, and local development launch |
| `wall_follow_benchmark` | Passive collector and benchmark lifecycle launch |
| `wall_follow_bridge` | Complete gem5 attempt launch, topic routes, guest deployment settings, and hardware model |
| `benchmarking/` | Configuration validation, result loading, metrics, and comparison; no ROS imports |
| `scripts/` | One suite runner and offline comparison/dashboard tools |
| `experiments/` | Sweep definitions |
| `tests/` | Controller, recording, orchestration, analysis, and dashboard checks |

Four launch files remain: `wall_follow_robot/application.launch.py` is the public
local development entry point; `wall_follow_robot/robot.launch.py` starts just the
controller for composition and guest execution. Each benchmark package has a
`benchmark.launch.py` for a complete native or gem5 attempt, called by the runner.
`session.json` stages the guest; it is not a separate world/application launcher.

Worlds and benchmark controller files are generated per attempt and retained with
the results. There are no checked-in default `world.sdf` or `controller.yaml` files.
The world generator is shared with local development, and controller defaults live
in the controller and are checked against the benchmark defaults by tests.

## Define a sweep

A JSON configuration contains `fixed`, `sweep`, and `repetitions`:

```json
{
  "repetitions": 3,
  "fixed": {"duration": 120.0, "wall_timeout": 7200.0},
  "sweep": {
    "l1": [
      {"l1i_size": "8KiB", "l1d_size": "8KiB"},
      {"l1i_size": "32KiB", "l1d_size": "32KiB"}
    ],
    "l2_size": ["128KiB", "1MiB"]
  }
}
```

Each sweep dimension varies independently. Parameters inside a bundle vary
together: this example produces **four configurations × three repetitions**,
not an independent L1I × L1D × L2 sweep. All bundles in one dimension must set
the same parameters. A parameter may appear in only one dimension or in `fixed`.
Omitted parameters use the [controller](benchmarking/wall_follow_benchmark/configuration.py),
[hardware](benchmarking/wall_follow_benchmark/hardware.py), and
[launch](scripts/run_experiments.py) defaults.

`duration` is the robot observation window in simulated seconds, starting at the
first recorded command. `wall_timeout` limits host wall time, including startup. Hardware
parameters require gem5. Other sweep parameters include `control_hz`, `speed`,
`lidar_hz`, `lidar_samples`, `noise_std`, and arena dimensions.

## Run a suite

Run these commands from `ros/wall_follow_ws`, with the built workspace sourced:

```bash
python3 scripts/run_experiments.py --gem5 \
  --config experiments/cache_hierarchy.json --output results/cache-hierarchy
```

The [cache hierarchy preset](experiments/cache_hierarchy.json) crosses three paired
L1 capacities with three L2 capacities and repeats each combination three times.
Default gem5 resources are under `../../gem5/resources`; override them with
`--gem5-root`, `--image`, or `--kernel`. Only one gem5 suite can run at a time
because its bridge sockets are shared. Leave Gazebo's Play/Step/Reset controls
alone during synchronized runs.

For a local controller/sensor sweep:

```bash
python3 scripts/run_experiments.py --config experiments/demo.json --output results/local
```

The runner validates the plan, prepares each world and controller file, then
launches attempts sequentially. Each attempt has its own directory and log.
Live progress shows the attempt's settings, simulation advance, task percentage,
and an approximate remaining task time. Boot is reported separately.

| Option | Purpose |
| --- | --- |
| `--run-id ID` | Execute a selected run ID; repeat in original plan order |
| `--dry-run` | Print the validated plan without launching or writing results |
| `--resume` | Continue the same saved plan; skip successful runs and retry failures |
| `--keep-going` | Continue after failed attempts; still return failure |
| `--quiet` / `--verbose` | Show attempt summaries only / also stream launch logs |
| `--collect-only` | Skip dashboard and summary generation |
| `--warmup SECONDS` | Exclude initial robot samples from task metrics; default 0 |
| `--architecture LABEL` | Label this suite; defaults to `gem5` or `local` |

Use a fresh output directory or `--resume`. Retries preserve earlier attempts.
An attempt succeeds only when the launch exits successfully and the collector
finishes its observation window. Timeouts, lost robot commands, and interruptions
remain recorded as failed attempts and are excluded from comparisons by default.

## Read the results

See the [example dashboard](../../docs/examples/wall-follow-dashboard.html) for
a hardware and workload sweep covering 62 of 64 planned runs. GitHub displays
HTML source; download the file and open it in a browser to use the interactive
report offline.

Open `<suite>/comparison/dashboard.html` in a browser. It works offline.
The dashboard has three metric sets:

- **Robot task:** time-weighted wall-error RMSE/MAE, maximum error, distance
  travelled after warmup, and ground-truth coverage. Distance is path length,
  not unique wall coverage or completed laps.
- **Architecture:** IPC, system instruction throughput, instructions, core-cycles,
  cache misses, and misses per thousand instructions (MPKI). These cover the whole
  gem5 workload region, including the guest OS and bridge activity. Core-cycles
  sum the ROI CPUs' `numCycles`; KVM boot CPUs are excluded. IPC divides total
  instructions by that sum: a cycle-weighted average across cores, not total
  system instructions per elapsed cycle. Adding cores can lower this IPC while
  increasing system throughput. Use instructions per simulated second to compare
  total instruction throughput across core counts or clock rates; use robot-task
  metrics to assess useful controller progress. L1 misses sum across cores and L2
  misses across banks. MPKI divides those totals by total instructions, rather
  than averaging per-core miss rates. Counts include OS and bridge work and do not
  measure controller-only work or parallel speedup. Ratios are computed within
  each repetition, then averaged with equal weight. Single-core gem5 uses
  unnumbered CPU/cache names; multicore uses numbered names. Both are supported.
- **Simulation:** overall simulated seconds per host wall second, gem5/Gazebo
  phase rates and wall times, settling time, and startup time. Overall throughput
  excludes startup. Local runs report collection throughput.

Summary panels use bar charts. Choose the sweep dimension to group bars; labels
include the remaining settings. Hover for configuration and repetition counts.
Large and small values use scientific notation. Signal and trajectory charts
have a **Maximize** button; close the expanded view with **Close** or Escape.
Warmup defaults to zero so short runs retain their task measurements. If an
explicit warmup removes the whole window, the task panel explains the missing data.

Repetitions with identical settings and provenance are averaged with equal weight.
Error bars show sample standard deviation; one repetition has no SD estimate.
Missing measurements are unavailable, never zero. Warmup affects task metrics;
simulation timings use all completed synchronization intervals. Ground-truth
error uses fresh recorded poses, not the robot's estimated wall distance.

Each attempt retains `samples.csv`, `poses.csv`, `metadata.json`, `experiment.json`,
`attempt.json`, and `launch.log`; gem5 attempts also retain `timing.csv` and
`gem5/stats.txt`. Comparisons export aggregate and per-run JSON/CSV summaries.
To compare suites or rebuild a report without ROS:

```bash
python3 scripts/compare_experiments.py results/cache-hierarchy results/another-suite \
  --output results/comparison
```

Use `--include-incomplete` to inspect partial attempts separately. Keep all raw
files when copying results. Analysis supports the current host-recorded schema
and suite format only.

## Checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=benchmarking python3 -m pytest -q tests
```

Tests cover controller behavior, recording, sweeps, orchestration, metrics, and
dashboard controls. See [the short sensitivity preset](experiments/timing_extreme.json)
for a four-case clock/cache check.
