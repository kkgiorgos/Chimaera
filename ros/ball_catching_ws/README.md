# Native FR3 visual cup catching

For local gem5/Ramulator interference experiments, see the
[memory benchmark guide](../../docs/ball-catching-memory-contention.md).
For portable isolated workers and the 50/80/90% access-pattern suite, see the
[Docker workflow](../../containers/ball-catching/README.md).

ROS 2 Humble / Gazebo Fortress application using the upstream FR3 arm with a
rigid cup, stereo RGB perception, ballistic flight estimation, interception and
braking, and computed-torque arm control. The robot receives images, calibration,
cup geometry, and joint feedback. Throw settings and ground truth stay on the
simulation host for experiment generation and passive scoring.

## Build and run

Run from the repository root with native ROS/Gazebo, a C++17 compiler, Eigen3,
Orocos KDL, ROS URDF, OpenCV development libraries, json-c, and pkg-config.
Python NumPy/xacro support scene generation; pytest supports workflow checks.
The description dependency is `ros/franka_ws/src/franka_description`.

```zsh
source /opt/ros/humble/setup.zsh
colcon --log-base ros/ball_catching_ws/log build \
  --base-paths ros/franka_ws/src/franka_description ros/ball_catching_ws/src \
  --build-base ros/ball_catching_ws/build \
  --install-base ros/ball_catching_ws/install \
  --cmake-args -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
source ros/ball_catching_ws/install/local_setup.zsh
python3 ros/ball_catching_ws/scripts/run_cup_examples.py \
  --output ros/ball_catching_ws/results/cup-demo
```

Use `.bash` setup files in Bash. Output directories must be new or empty.
The suite runs four lofted throws and a deliberate miss, restarting the simulation
for each throw. Select individual cases with `--cases default diagonal fast inclined miss`.
Use `--flat` for the earlier, shorter flat throws as challenges. Completed misses
are recorded outcomes; an infrastructure failure returns 2 and retains logs.

For a single lofted demonstration:

```zsh
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/single \
  --position 1.8 0.04 3 --direction -1 0 2 \
  --camera-position -2 0 2 --resolution 1280 960
```

The single-trial runner starts headless Gazebo, isolates ROS/Gazebo discovery,
waits for arm and camera readiness, and stops after scoring. It returns 0 for a
catch and 1 for a miss. Its default throw is the original flat challenge from
`[2.08, 0.04, 2]` m at 3 m/s along `[-1, 0, 0]`. Configure `--position`,
`--direction`, `--speed`, `--duration`, `--camera-position`, `--camera-hz`,
`--resolution`, and `--wall-timeout`; add `--gui` to show Gazebo.
Each trial runs for 1.5 simulated seconds after launch by default. Change this with
`--duration 2.0` on either runner. Every outcome is evaluated at the deadline: a
catch succeeds if the ball is inside the cup then, even if it entered recently.
An earlier catch that escapes before the deadline fails. Gazebo pauses when the
result is published, and the runner shuts down the processes. Evaluation occurs
on the first physics step at or after the requested deadline (within one
physics step). `--wall-timeout` separately limits startup or stalled runs in wall time.

For interactive launch, run `ros2 launch ball_catching_sim application.launch.py`.
Launch arguments also configure `cup_radius`, `cup_depth`, `physics_step`,
`duration` (default `1.5`), and `auto_throw`. With `auto_throw:=false`, queue a throw using
`ros2 service call /experiment/throw std_srvs/srv/Trigger '{}'` after readiness.
Set `robot:=false` to supply the robot computation through the interfaces below.

## Cup and control

Every trial starts at `[0, 0, 0, -0.14, 0, 0.50, pi/4]` radians. This tall home
satisfies the arm's limits; the cup starts tilted and rotates upward during the
approach. Other starting poses are rejected. The cup has a 0.12 m wall radius,
0.14 m depth, a bottom and 20 physical walls, and an offset rigid bracket that
keeps the wrist clear of the opening. The ball radius is 33.5 mm and mass is 57 g.

The robot runs three native processes: `perception`, `intercept`, and
`effort_control`. Perception pairs matching stereo timestamps, detects and
triangulates the ball, and fits position and velocity under gravity. Planning
searches feasible interception heights and incoming-direction cup velocities,
then publishes continuous approach and braking trajectories. The outer planner
runs at 30 Hz with searches separated by at least 60 ms. Effort control runs
at 250 Hz with joint feedback, dynamics compensation, and bounded torques.

| Interface | Type | Meaning |
|---|---|---|
| `/stereo/{left,right}/image_raw` | `sensor_msgs/Image` | Rendered synchronized RGB images |
| `/robot/joint_states` | `sensor_msgs/JointState` | Seven arm positions and velocities, 250 Hz |
| `/robot/ball_state` | `nav_msgs/Odometry` | Visually estimated world position and velocity |
| `/robot/ball_geometry` | `geometry_msgs/Vector3Stamped` | Visually estimated radius in `vector.x` |
| `/robot/joint_trajectory` | `trajectory_msgs/JointTrajectory` | Approach and braking segments |
| `/robot/effort_command` | `sensor_msgs/JointState` | Seven named joint torques |
| `/robot/{ready,perception_ready}` | `std_msgs/Bool` | Home and camera readiness |
| `/evaluation/{ball_pose,result}` | `PoseStamped`, `String` | Host-only ground truth and outcome |

The host applies joint efforts and a one-step launch velocity. Ball flight and
contact then evolve through native physics. At the deadline, success requires sphere containment in the polygonal cup
cavity. Continuous containment duration is recorded for inspection and resets
on escape; it is not a minimum success requirement. Evaluation data never enter robot control.

## Records and replay

Each trial preserves `experiment.json`, `metadata.json`, `result.json`,
`ground_truth.csv`, perception/interception/control JSONL records, the generated
URDF/world, and launch logs. Metadata records source and installed artifact
fingerprints. The example suite writes an incremental `experiment_manifest.json`.

Open the generated `dashboard.html` locally to select throws, replay recorded
ball and arm motion, scrub or slow playback, and inspect computation timings.
The page embeds its robot assets and works offline. Rebuild a report with:

```zsh
python3 ros/ball_catching_ws/scripts/report.py ros/ball_catching_ws/results/cup-demo \
  --output ros/ball_catching_ws/results/cup-demo/dashboard.html
```

Flight and visual reaction windows end at the start of the final uninterrupted
cup containment; misses show these
windows as unavailable. Incoming speed uses the sample before cup containment
or an inferred earlier impact. Replay interpolates recorded physics samples.
Perception, planning, and controller computation use native steady wall time;
observation age, cadence, flight, and braking schedules use simulation time.
Physics/render computation and bridge CPU time are not measured separately.
Timing statistics and replay stop at final evaluation and exclude shutdown
activity. The overview and timeline show the configured duration and evaluation
time. These native timings are not gem5 instruction timings.

## Tests

```zsh
source /opt/ros/humble/setup.zsh
source ros/ball_catching_ws/install/local_setup.zsh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONPATH=ros/ball_catching_ws/src/ball_catching_sim:$PYTHONPATH \
  python3 -m pytest -q ros/ball_catching_ws/tests
colcon --log-base ros/ball_catching_ws/log test \
  --build-base ros/ball_catching_ws/build \
  --install-base ros/ball_catching_ws/install \
  --packages-select ball_catching_robot ball_catching_sim
colcon --log-base ros/ball_catching_ws/log test-result --test-result-base ros/ball_catching_ws/build --verbose
```

Native tests cover stereo detection, ballistic estimation, arm dynamics and
limits, home rejection, continuous braking, and cup containment and deadline scoring.
Python tests cover scene validation and generation, report timing, and offline
asset embedding. Record full simulation results separately from unit tests.

Trajectory feasibility is sampled, and physical contact/tracking can depart
from commanded trajectories. Cup contact materials and camera geometry are
simulation assumptions, not a hardware validation. See the
[cup design](../../docs/ball-catching-design.md),
[recorded validation](../../docs/ball-catching-validation.md), and
[home, braking, and timing reference](../../docs/ball-cup-home-braking.md).

For gem5 memory experiments, KVM boots Linux and the selected detailed CPU handles
arm/camera readiness without aggression. The orchestrator saves that readiness
once on its preparation machine, including a host readiness replay and the exact
backing disk. Cases restore without KVM, warm up under their selected pressure
for 50 simulated ms by default, then reset statistics immediately before throwing.
Ball position/direction/speed remain independent host-side launch settings.
Rebuild gem5, transport, `chimaera_ros_bridge` and `ball_catching_sim`, then prepare
a new experiment and checkpoints after updating.
See [the experiment protocol](../../docs/ball-catching-memory-contention.md)
for checkpoint sharing and validation limits.
