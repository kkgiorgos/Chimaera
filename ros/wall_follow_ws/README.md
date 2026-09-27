# Wall following: C++ ROS runtime and standalone Python benchmarks

The ROS 2 Humble package in `src/wall_follow_benchmark` is an `ament_cmake`
package. Its C++ controller estimates a right wall using a deterministic
consensus fit, publishes bounded velocity commands, accepts live controller
parameter changes, and records raw observations and timing data.

Everything that defines or evaluates an experiment lives outside the ROS package:

- `scripts/run_experiments.py`: validates sweeps, generates Gazebo worlds and
  parameter files, starts ROS applications, retains attempts, and handles retries.
- `benchmarking/wall_follow_benchmark`: standalone Python world generation,
  configuration validation, metrics, repetition aggregation, and plotting.
- `scripts/compare_experiments.py` and `scripts/build_dashboard.py`: static plots
  and an interactive dashboard.
- `tests`: Python suite tests and compiled C++ controller tests.

Analysis, plan validation, and dashboard generation need no ROS installation or
ROS Python imports. ROS launch files remain Python launch descriptions; they
only start and supervise runtime processes, using externally prepared inputs.

## Build

From this directory, with ROS Humble, Gazebo Fortress, `ros_gz_sim`, and
`ros_gz_bridge` installed:

```bash
source /opt/ros/humble/setup.bash
colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
python3 -m pip install -e benchmarking
```

Use `.zsh` setup files with zsh. NumPy and Matplotlib are dependencies of the
standalone analysis package, not dependencies of the ROS package.

## Run a suite

```bash
python3 scripts/run_experiments.py \
  --config experiments/demo.json \
  --architecture desktop --output results/desktop
```

The default `experiments/demo.json` sweeps control and lidar rates. Add
`arena_width` or `arena_height` to a sweep to vary arena dimensions. Configuration files
contain `fixed`, `sweep`, and `repetitions`; sweep lists form a Cartesian product.
Use `--dry-run` to validate and print the plan without ROS or file writes,
`--resume` to skip completed runs and retain failed attempts when retrying,
`--keep-going` to continue after failures, and `--no-plot` to collect data only.
`gui` is a JSON boolean; `duration` is simulation seconds and `wall_timeout` is
wall-clock seconds. Each attempt receives its own process group and output folder.

For a single run, supply a configuration with `repetitions: 1`, an empty `sweep`,
and the desired parameters in `fixed`. This uses the same preparation and
provenance path as a larger suite.

## Runtime interface

The `controller` executable subscribes to `/scan` (`LaserScan`) and
`/ground_truth` (`Odometry`) with sensor-data QoS and publishes `/cmd_vel`
(`Twist`). The control timer uses the ROS clock, including simulation time.
Missing, stale, or invalid scans stop translation; a front obstacle triggers a
bounded left turn. Completion publishes a stop command and closes the data files.

Controller parameters and defaults:

| Parameter | Default |
| --- | --- |
| control_hz | 20.0 |
| target_distance | 0.8 |
| speed | 0.35 |
| kp / heading_gain | 1.8 / 2.0 |
| max_yaw_rate | 1.2 |
| front_stop | 0.65 |
| scan_timeout | 0.3 |
| beam_stride / min_points | 1 / 6 |
| sector_start / sector_end | -110.0 / -40.0 degrees |
| fit_threshold | 0.04 |

`duration` and `output_dir` are startup-only parameters. Controller parameters
can be changed with `ros2 param set /wall_follower …`; rate changes recreate the
ROS timer and accepted changes are recorded in metadata.

The runtime can also be launched with externally prepared files:

```bash
ros2 launch wall_follow_benchmark benchmark.launch.py \
  world:=/absolute/path/world.sdf \
  parameters_file:=/absolute/path/controller.yaml \
  output_dir:=/absolute/path/new-run gui:=false wall_timeout:=600
```

`controller.yaml` contains a `wall_follower.ros__parameters` mapping. Arena and
sensor configuration belong to the external suite, not the controller.

## Data and analysis

Schema version 2 records `samples.csv` (commands, wall estimates, latest pose and
stamp, scan age, simulation/wall timing, process CPU time and RSS) and `poses.csv`
(every received ground-truth position). `metadata.json` records controller
settings, parameter events, C++ source hashes, compiler/platform information,
and completion status. The suite writes `experiment.json`, `controller.yaml`,
`world.sdf`, `attempt.json`, and `launch.log` alongside those raw files.

The Python loader derives ground-truth wall distance, tracking error, pose age,
and total path length. Poses older than 0.2 simulation seconds do not contribute
valid ground-truth errors. Per-sample target distance preserves the meaning of
live parameter changes. Raw files are never rewritten by analysis. Analysis
requires schema version 2 raw data produced by the C++ runtime.

```bash
python3 scripts/compare_experiments.py results/desktop --output results/comparison
python3 scripts/build_dashboard.py --help
```

Comparisons report time-weighted error metrics, coverage, timing, CPU usage, and
memory. Repetitions receive equal weight; bands are sample standard deviations,
not confidence intervals. Runs with different configurations or runtime
provenance are grouped separately.

## Checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=benchmarking python3 -m pytest -q tests
```

These checks need Python dependencies and a C++17 compiler, but no ROS. They
compile and exercise the actual C++ estimator against known wall geometry,
and test world generation, raw-data analysis, aggregation, and suite orchestration.
