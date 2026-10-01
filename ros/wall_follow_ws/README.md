# Wall-follow benchmark

This example measures how a robot tracks a wall under different controller,
sensor, and simulated hardware settings. It also measures the host time Chimaera
needs to advance the simulation.

The C++ robot controller receives lidar scans and publishes velocity commands.
Gazebo provides the arena and ground-truth poses. A separate host collector records
commands, poses, and simulation time; the robot contains no benchmark code.
In a Chimaera run, the controller runs inside gem5 and Gazebo runs on the host.
The bridge advances both simulators in synchronized steps. In a local run,
the controller and Gazebo both run on the host.

## Setup

You need ROS 2 Humble, Gazebo Fortress, `ros_gz_sim`, `ros_gz_bridge`, and NumPy.
Chimaera runs also need a built x86 gem5, KVM access, and a ROS Humble guest image.
See the [bridge guide](../chimaera_ros_ws/README.md) for transport dependencies and
image preparation.

For Chimaera, build from the **repository root**:

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
```

Deploy the built guest runtime and robot once, with the image **offline**:

```bash
./deploy_guest.sh /absolute/path/to/disk.img 2
```

The second argument is the image's root partition. Deployment requires sudo;
build the guest binaries in an environment matching the image. Redeploy after
changing guest code. Sweep settings are injected at boot without redeploying.
Use `.zsh` setup files when sourcing from zsh.

For local runs only, build inside `ros/wall_follow_ws` instead:

```bash
source /opt/ros/humble/setup.bash
colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
python3 -m pip install -e benchmarking
```

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
first command. `wall_timeout` limits host wall time, including startup. Hardware
parameters require gem5. Other sweep parameters include `control_hz`, `speed`,
`lidar_hz`, `lidar_samples`, `noise_std`, and arena dimensions.

## Run a suite

Run these commands from `ros/wall_follow_ws`, with the built workspace sourced:

```bash
python3 scripts/run_gem5_experiments.py \
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

Open `<suite>/comparison/dashboard.html` in a browser. It works offline.
The dashboard has three metric sets:

- **Robot task:** time-weighted wall-error RMSE/MAE, maximum error, distance
  travelled after warmup, and ground-truth coverage. Distance is path length,
  not unique wall coverage or completed laps.
- **Architecture:** IPC, instructions, cycles, cache misses, and misses per
  thousand instructions. These cover the whole gem5 workload region, including
  the guest OS and bridge activity.
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
