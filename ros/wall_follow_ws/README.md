# Lidar wall-following benchmark — ROS 2 Humble / Gazebo Fortress

A standalone `ament_python` package: differential-drive robot, 360° 2D GPU lidar,
configurable rectangular arena (12 × 8 m by default), closed-loop right-wall controller, experiment recorder,
and offline comparison plots. No downloads of models or Nav2 dependencies.

## Build and run

On Ubuntu 22.04 with ROS 2 Humble and Gazebo Fortress:

```bash
sudo apt install ros-humble-ros-gz python3-colcon-common-extensions python3-numpy python3-matplotlib python3-pytest
source /opt/ros/humble/setup.bash
cd wall_follow_ws
colcon build --symlink-install
source install/setup.bash
ros2 launch wall_follow_benchmark benchmark.launch.py \
  output_dir:=results/baseline architecture:=desktop duration:=120.0
```

Use `setup.zsh` instead of `setup.bash` in zsh. Run from this workspace directory
so relative result paths match the examples. Every experiment needs a fresh output
directory. The controller exits after the requested **simulation** duration and
launch shuts down the simulator. Ctrl-C interrupts a run; its metadata remains
`completed: false`. Launch has a configurable wall-clock watchdog (`wall_timeout`,
default 600 s) for missing clock, rendering failures, or very slow simulation.

For unattended runs add `gui:=false` (Fortress server with EGL headless rendering).
A functioning Ogre2/OpenGL rendering backend is still required for GPU lidar.
Run only one experiment per ROS/Gazebo partition, or assign distinct
`ROS_DOMAIN_ID` and `IGN_PARTITION` environment variables to concurrent runs.

## Controller and knobs

The robot starts near the southwest corner, facing +x, one metre from the south
wall. The default arena gives the original start pose (-4, -3). It fits a line by total least squares to
right-side lidar returns after deterministic consensus outlier rejection. Two
low-friction caster supports keep the lidar plane level during braking. Steering is
`yaw = -kp * (distance - target_distance) + heading_gain * wall_heading`, clipped
to the yaw limit. Forward speed decreases during turns. A front obstacle triggers
a left pivot; missing wall/front data or stale scans command zero motion.
This is a simple rectangular-room baseline, not a general navigation planner.

All the following are launch arguments **and live ROS parameters**:

| Parameter | Default | Meaning |
|---|---:|---|
| `control_hz` | 20.0 | Timer rate in simulation time (1–500 Hz) |
| `target_distance` | 0.8 | Lidar/robot-centre distance to wall, metres |
| `speed` | 0.35 | Maximum commanded forward speed, m/s |
| `kp` | 1.8 | Distance feedback gain |
| `heading_gain` | 2.0 | Wall-heading feedback gain |
| `max_yaw_rate` | 1.2 | Angular command limit, rad/s |
| `front_stop` | 0.65 | Front-sector obstacle threshold, metres |
| `scan_timeout` | 0.3 | Maximum scan age in simulation seconds |
| `beam_stride` | 1 | Use every Nth valid wall-sector return |
| `sector_start`, `sector_end` | -110.0, -40.0 | Right-wall fit sector, degrees |
| `min_points` | 6 | Minimum returns for a line fit |
| `fit_threshold` | 0.04 | Consensus inlier tolerance, metres |

```bash
ros2 param set /wall_follower control_hz 40.0
ros2 param set /wall_follower beam_stride 4
ros2 param set /wall_follower speed 0.25
```

Changes are validated and timestamped in `metadata.json`. Use separate runs for
controlled comparisons; live changes are intended for exploration. Parameter
numeric types matter: supply decimals for floating-point ROS parameters.

These are **launch-only** experiment settings:

| Argument | Default | Meaning |
|---|---:|---|
| `arena_width` | 12.0 | Inner arena size along x, metres (minimum 4) |
| `arena_height` | 8.0 | Inner arena size along y, metres (minimum 4) |
| `lidar_hz` | 20.0 | Sensor generation rate |
| `lidar_samples` | 720 | Full-circle beam count |
| `noise_std` | 0.0 | Gaussian range noise standard deviation, metres |
| `physics_step` | 0.001 | Physics step in seconds |
| `duration` | 120.0 | Simulation-time run length, including startup |
| `architecture` | unspecified | Free-text platform/configuration label |
| `gui` | true | GUI versus headless server |
| `wall_timeout` | 600.0 | Wall-clock watchdog in seconds |

## Automated experiment suites

From `wall_follow_ws`, after sourcing ROS and `install/setup.bash` (or `.zsh`):

```bash
# Preview the default 3 frequencies × 3 repetitions without running or writing files.
python3 scripts/run_experiments.py --architecture desktop \
  --output results/desktop_rates --dry-run

# Run the suite sequentially and automatically generate comparison plots.
python3 scripts/run_experiments.py --architecture desktop \
  --output results/desktop_rates

# Resume the exact same suite: skip successes, retain and retry failures.
python3 scripts/run_experiments.py --architecture desktop \
  --output results/desktop_rates --resume
```

The default configuration is `experiments/control_frequency.json`. Edit a copy to
set `repetitions`, shared `fixed` launch arguments, and `sweep` lists. All combinations
of sweep values are run (Cartesian product). JSON numbers are converted to the
correct ROS parameter types. Each parameter must appear in either `fixed` or
`sweep`, never both. The default suite takes roughly 18 simulation minutes.

Example arena-size sweep (3 widths × 2 heights × 3 repetitions):

```bash
python3 scripts/run_experiments.py --config experiments/world_size.json \
  --architecture desktop --output results/desktop_world_sizes
```

World dimensions describe the inner rectangle, centred at (0, 0). Resizing adjusts
the walls, ground display, spawn position, ground-truth error and plotted arena
outline; robot size, target distance, lidar range and controller gains stay fixed.
The x spawn is `-arena_width/2 + min(2, arena_width/4)` and y is
`-arena_height/2 + 1`. Width and height must each be at least 4 m. These are
launch-only parameters; changing arena size requires a new simulation.

For a single custom world:

```bash
ros2 launch wall_follow_benchmark benchmark.launch.py \
  arena_width:=16.0 arena_height:=10.0 output_dir:=results/large_world
```

Each suite saves the expanded settings in `suite.json`, with individual attempts
under `runs/case_001_rep_01/attempt_001/`. Each attempt has a `launch.log`, an
`attempt.json` status/command record, and the benchmark's normal result files.
The runner checks both process exit and completion metadata. It stops on a failed
run unless `--keep-going` is set, and returns nonzero if any run or plotting failed.
`--resume` requires the same settings and architecture; failed attempts are never
overwritten. Ctrl-C stops the active launch and preserves partial results.
The launch watchdog is backed by a process timeout with a 30 s shutdown allowance.
Use distinct ROS domains/Gazebo partitions if running suites concurrently, and
never run two runners against the same suite directory.

Successful runs are plotted automatically into the suite's `comparison/` directory.
Use `--no-plot` to defer plotting or `--warmup 0` for short smoke tests. By default,
5 simulation seconds are excluded. Repetitions with identical recorded settings
are aggregated into configuration means and standard deviations in both the static
plots and the interactive website (`comparison/dashboard.html`).

Copy complete suite directories from other machines, then compare them together:

```bash
python3 scripts/compare_experiments.py \
  results/desktop_rates results/arm_rates \
  --output results/architecture_comparison
```

The comparison script recursively discovers runs, deduplicates overlapping input
paths, skips incomplete/failed attempts, and writes `included_runs.json` alongside
the plots and summaries. It also accepts manually collected run directories and
older default-size results. `--include-incomplete` opts into partial runs for
diagnosis (each still needs at least two samples). Plotting only requires Python,
NumPy and Matplotlib; it does not require a running or sourced ROS installation.
For different arena sizes, the trajectory plot draws each recorded arena outline.
A fixed duration covers fewer corners/laps in larger arenas; account for that
when comparing task difficulty and tracking errors.

## Interactive comparison website

Every call to `scripts/compare_experiments.py` now creates `dashboard.html` alongside
the static plots. Suite automation calls it automatically. Open the HTML file in
Firefox, Chrome, or another modern browser: no server, internet, CDN, or additional
JavaScript packages are needed. The file embeds the comparison data and can be shared.
It includes the recorded local run paths and configuration metadata.

To generate just the website (from `wall_follow_ws`):

```bash
python3 scripts/build_dashboard.py results/test_rates \
  --output results/test_rates/dashboard.html

# Or compare multiple suites, including results copied from another architecture:
python3 scripts/build_dashboard.py results/desktop_rates results/arm_rates \
  --output results/architecture_dashboard.html
```

Select configuration groups in the sidebar. Each represents the repetitions with
matching settings; expand its member list to see which run directories contributed.
Use search, Select shown, and Clear selection to choose the comparison. The table
highlights differing configuration values, with a toggle for shared settings and
software/hardware details. Live parameter changes are visible separately and in
the differences table. Chart selectors switch metrics and signals; hover shows
values and repetition counts, and time limits narrow the plotted interval.
Selections are retained in the page URL. Export downloads group summaries and
configuration values as CSV. The layout also supports narrow screens.

This is an offline snapshot. Regenerate it after adding results. `--warmup` controls
the summary exclusion period at generation time; the website's time limits affect
charts only. `--max-points` (default 1500) controls the aligned time grid for charts,
not the underlying scalar metric calculations. Failed/incomplete attempts are
excluded by default; `--include-incomplete` is for diagnosis and keeps completion
states in separate groups. Legacy runs without arena dimensions use the original
12 × 8 m arena. Unknown settings are shown as “Not recorded”.

## How repetitions are aggregated

A group must match the recorded architecture, initial controller settings, sensor
and physics settings, arena dimensions, duration, GUI mode, execution provenance,
controller/world source hashes, completion state, and live parameter-change history.
Different live-change timestamps therefore create separate groups. Presentation-only
source changes do not split groups. Directory names and repetition numbers do not
affect grouping. Use explicit architecture labels when recording experiments:
missing machine metadata cannot reveal unrecorded hardware differences.

Scalar metrics are computed **per run first**, then averaged with equal weight per
run. This avoids bias from different sample counts. Bars show the mean and sample
standard deviation (`ddof=1`), not standard error or a confidence interval. No SD
is estimated for a single repetition. Missing metrics are excluded only from that
metric's calculation, with its valid `n` reported. No outliers are silently removed;
exports include mean, SD, median, minimum, maximum, and valid count to inspect them.
For example, computation p95 is the average of per-run p95s, not the p95 of pooled
samples. Summary CSV columns now have suffixes such as `rmse_m_mean`, `rmse_m_std`,
and `rmse_m_n`; each row represents a configuration, not an individual run.

Signal traces interpolate each repetition onto a common elapsed-simulation-time
grid, using only the shared recorded time interval. Lines show pointwise means and
bands show ± one sample SD. Missing values remain missing; hover displays the
available count. There is no extrapolation. Groups without overlapping time
intervals retain scalar metrics but have no mean trace. The mean x/y trajectory is
a time-aligned average, not an actual observed path; differences in corner timing
can smooth the apparent path. The chart grid may miss brief events, so use the
full-sample scalar extrema and raw CSV when inspecting spikes.

The same loading, grouping, and aggregation implementation feeds both output types.
`configuration.png` displays differing settings; `configuration.csv`/`.json` retain
full shared and differing values. `per_run_summary.json` retains original per-run
metrics and paths for auditing an aggregate.

## Compare experiments

For example, vary control rate while holding the sensor and physics settings fixed:

```bash
for hz in 10 20 40; do
  ros2 launch wall_follow_benchmark benchmark.launch.py \
    gui:=false duration:=120.0 control_hz:=${hz}.0 \
    architecture:=desktop output_dir:=results/desktop_${hz}hz
done
ros2 run wall_follow_benchmark plot results/desktop_*hz --output results/comparison
```

The plotting command writes aggregated `comparison.png` and `metrics.png`,
`configuration.png`/`.csv`/`.json`, group-level `summary.csv`/`.json`, and
`per_run_summary.json` for traceability. The comparison automation additionally
writes the interactive `dashboard.html`.
It accepts run directories copied from other machines. Each run contains:

- `samples.csv`: simulation and monotonic elapsed time, actual timer intervals,
  scan age, controller state, estimated wall distance, ground-truth pose/error,
  commands, cumulative path length, computation time, CPU time, and peak RSS.
- `metadata.json`: initial settings, live changes, source SHA-256 hashes, platform and completion status.
- `experiment.json`: sensor/physics settings, arena dimensions, architecture label, GUI setting.
- `world.sdf`: exact generated arena and robot used for the experiment.

Summary metrics include time-weighted RMSE/MAE/max error, valid ground-truth
coverage, stopped/stale fractions, distance travelled, computation p95/max,
scan-age p95, actual control rate, wall-time interval p95, real-time factor, controller CPU core fraction, and peak RSS.
Plots show trajectory, ground-truth error, computation cost, and scan age.
The default warmup exclusion is 5 simulation seconds (`--warmup 0` disables it).
CPU, RSS, and real-time factor summarize the whole recorded run.

Ground-truth error is `min(arena_width/2-|x|, arena_height/2-|y|) - target_distance`, from Gazebo's
OdometryPublisher, **not wheel odometry or the controller's fitted distance**.
At corners it measures the nearest arena wall, which may differ from the fitted
right wall. It becomes negative outside the arena. Ground truth older than 0.2 s
is excluded and reduces reported coverage. Error is weighted by simulation-time
intervals and includes stopped states and corners; the final sample has zero
weight. Never rank a run by error alone: a stationary robot can have low error.
Compare coverage, distance travelled, stopped fraction, and completion too.
There is no contact sensor: stopped fraction is not a collision count, and the
wall distance is centre clearance, not body clearance.

`compute_ms` covers the control callback through command publication, excluding
CSV recording. CPU and RSS measure the **controller process only**, including
logging and ROS overhead, not Gazebo, bridge, GPU, energy, or whole-system usage.
CPU fraction 1.0 means one fully occupied core. Scan age uses simulation time;
it is not a wall-clock sensor-to-actuator latency measurement. The recorded
`dt_wall` and `dt_sim` expose scheduling jitter but do not imply hard real-time
execution. A paused simulator also pauses the controller timer.

For architecture comparisons, use the same code, duration, arena dimensions, physics/sensor
settings, warmup, GUI mode, and target speed; repeat each configuration across
several trials. Record OS, CPU governor, affinity, compiler/Python versions and
external power measurements alongside the run. Simulator and controller sharing
hardware confounds results: report real-time factor and consider running Gazebo
on a fixed host for controller-only comparisons. Fortress’s CLI does not expose a random seed here; noisy runs are stochastic.
Use repeated trials; even noiseless runs can differ with asynchronous ROS scheduling. A 120 s run exercises
multiple corners; longer runs are useful for repeated laps.

## Tests

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src/wall_follow_benchmark \
  python3 -m pytest -q src/wall_follow_benchmark/test
```

Tests cover steering signs, distance/heading estimation, obstacle/missing-data
behavior, parameter validation, world structure, and metric weighting.

References: [Fortress ROS integration](https://gazebosim.org/docs/fortress/ros2_integration/),
[Fortress sensors](https://gazebosim.org/docs/fortress/sensors/),
[DiffDrive parameters](https://gazebosim.org/api/gazebo/6/classignition_1_1gazebo_1_1systems_1_1DiffDrive.html).

## Validated local baseline

The package built successfully and all 13 unit tests passed. A headless 120 s run
with defaults completed a full circuit and shut down cleanly. With the default
5 s warmup exclusion it recorded 0.0846 m tracking RMSE, 38.14 m travelled,
100% ground-truth coverage, no stale-scan time, and real-time factor 1.000.
The local controller computation p95 was 2.46 ms; this is a measurement of this
host and run, not a portable performance guarantee. Live rate changes from
20 to 40 Hz and rejection of 0 Hz were also verified in a separate run.

Local artifacts (ignored by Git): `results/validation/` and
`results/validation_plots/{comparison.png,metrics.png,summary.csv,summary.json}`.

Automation and custom arena updates: all 25 tests pass, including failed-run
retries, resume protection, Cartesian sweep generation, and arena geometry/error
checks. Two automated 3 s headless runs (8 × 6 m and 12 × 6 m) completed and
generated comparison plots; older 12 × 8 m results also remain compatible. These
short runs validate the workflow, not full-course tracking in every arena size.

Interactive comparison validation: 31 tests passed, including equal repetition
weighting with different sample counts, missing-data counts, grouping by settings
and source version, overlapping time ranges, and HTML data escaping. Headless
browser checks covered selection, configuration differences, variability bands,
CSV export, bookmarked selection, time filters, and narrow-screen layout. The
existing `results/test_rates` suite was rendered as four groups of three repetitions.
