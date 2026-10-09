# Native FR3 visual airborne grasping

ROS 2 Humble / Gazebo Fortress application using the upstream FR3 description,
stock Franka Hand fingers, stereo RGB perception, visually estimated quadratic
drag, repeated interception planning, and computed-torque arm control. The
default experiment gently tosses tennis-sized balls above the stock fingers.
The `court-bounce` practice profile crosses the full court and physical net,
then permits two ground bounces before the airborne grasp. The robot
receives images and hardware feedback; launch parameters and
ball/contact ground truth belong exclusively to experiment generation and scoring.

## Build and run

Run from the repository root with native ROS/Gazebo, a C++17 compiler, Eigen3,
Orocos KDL, ROS URDF, OpenCV development libraries, json-c, and pkg-config.
Python NumPy/xacro support scene generation; pytest supports workflow checks.
The existing description dependency is `ros/franka_ws/src/franka_description`
(version 1.6.1). No libfranka build, MoveIt, Docker, or guest is required.

```zsh
source /opt/ros/humble/setup.zsh
colcon --log-base ros/ball_catching_ws/log build \
  --base-paths ros/franka_ws/src/franka_description ros/ball_catching_ws/src \
  --build-base ros/ball_catching_ws/build \
  --install-base ros/ball_catching_ws/install \
  --cmake-args -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
source ros/ball_catching_ws/install/local_setup.zsh
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --profile court-bounce --output ros/ball_catching_ws/results/demo
```

Use `.bash` setup files in Bash. Every output directory must be new or empty.
The runner starts headless Gazebo, isolates ROS/Gazebo discovery, waits for arm,
hand and camera readiness, and stops after scoring. Exit status is 0 for a catch,
1 for a miss; infrastructure errors retain logs and produce a traceback.
A GUI run uses `ros2 launch ball_catching_sim application.launch.py`.

The default `gentle` profile launches from `[0.5, 0, 1.1]` m with an **upward
1.5 m/s toss**, aimed to descend into the nominal grasp region `[0.5, 0, 0.8]` m.
The arm starts from fixed upright home and tracks the free flight from images.
Nominal flight to the target is about 0.44 s, with arrival about 2.84 m/s.
Gravity increases arrival speed above launch speed in this overhead example.
The upward arc provides observation time through physical flight, without an
artificial delay. Earlier five-throw passes at 1.5 and 2 m/s used a prepared
catching pose and do not qualify the upright-start version; see the validation record.

The optional `court` profile launches from `[23.77, 0, 2.5]` m across a physical
net, using a low arc and the same fixed upright home. Its default launch speed
is 20 m/s. These full-court points failed in the initial validation and remain
challenge cases. Simply lowering their launch speed cannot produce a gentle
full-court catch: an 18 m/s launch still arrives near 13 m/s in the current model.
The `gentle` and original `court` profiles prohibit floor contact before capture.
The new `court-bounce` profile explicitly permits two receiving-half impacts,
using 14 m/s practice lobs and a side-offset 1280×960
stereo pair. The robot visually detects a rebound and reacquires its flight.
[Physics choices and every adjustable default](../../docs/ball-court-practice.md)
are documented, including the 25 ms earlier closure used for motor startup.
Every gripper profile starts from fixed home
`[0°, −45°, 0°, −135°, 0°, 90°, 45°]`. Custom initial poses are rejected.
The robot can plan an approach toward its workspace centre once images show an
incoming ball, then refine the actual interception. All movement happens during
flight. The dashboard shows starting condition, approach timing, launch hand
position and displacement. Historical prepared-pose 5/5 passes are preserved
separately and do not qualify this starting condition. The updated court run
`court-upright-current-pose-validation` passed **5/5**, with 19.5–20.9 cm of
hand displacement during flight; see the validation record.
It is not a legal tennis return or a fast-serve claim.

The rigid ball is 67 mm in diameter and 57 g; default host drag coefficient is
0.55 at air density 1.225 kg/m³. The robot independently infers radius and drag
from visual observations. There is no wind, spin lift, or ball deformation.

```zsh
# One gentle toss, with optional presentation after grasping:
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/custom \
  --present

# Original flat cup challenge (use the lofted suite below for demonstrations):
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/cup --mode cup
```

The cup now starts from a tall vertical home, with an 8° elbow bend required by
joint limits, and rotates its opening upward during the visual approach. It
matches part of the incoming velocity and follows a bounded braking segment
instead of stopping abruptly at interception. Run the adapted lofted examples
and generate one dashboard with:

```zsh
python3 ros/ball_catching_ws/scripts/run_cup_examples.py \
  --output ros/ball_catching_ws/results/cup-vertical-demo
```

Use `--cases fast` for just the 5 m/s loft and `--flat` for the older flat
challenge geometry. The lofts and camera placement give the arm physical flight
time to move from vertical home. The dashboard includes split perception
timings, distributions and cadence, planned braking markers, and an interactive
pipeline reference. See [cup home, braking and timing definitions](../../docs/ball-cup-home-braking.md).

A single success does not qualify a five-throw parameter point. `--present`
raises/turns the held ball as far as a feasible bounded trajectory permits;
benchmarks omit presentation. `--retention` changes the physical hold criterion
and presentation waits for that hold before moving. The dashboard separately
reports whether the ball remains held at the gesture's end. The retention
defaults to 0.2 simulated seconds for the gripper, 1.0 for the cup. No attachment,
magnet, or privileged ball-position controller is used. Finite ball/finger
friction defaults to 1.0 and is configurable through `--grip-friction`.
The rubber tips are already part of the stock finger model; no pads are added.

The configured motor limits are an 80 mm hand opening and 50 mm/s per finger.
A conservative simulation convention splits a 50 N total grasp command between
two actuators;
each actuator is capped at 35 N. This respects the published 30–70 N continuous
force range without assuming 70 N per finger. It approximates the motor/servo,
not every internal behavior of the commercial hand.

Other runner options include `--position X Y Z`, `--camera-hz`,
`--resolution WIDTH HEIGHT`, `--camera-position X Y Z`,
`--drag-coefficient`, `--arc low|high`, `--target-orientation upward|forward` (legacy alias `--home-orientation`),
`--target-pitch` (legacy alias `--home-pitch`, reachability check only), `--gripper-lead`, `--bounces`, `--court-restitution`,
`--court-tangent-ratio`, and `--wall-timeout`. `--direction X Y Z` selects an
explicit, normalized direction instead of an aimed trajectory; combine it with
`--no-court` for diagnostic throws. Those manual throws do not receive the
court experiment's geometric preflight checks. Every run starts from its mode's fixed home;
reachability-check orientation options never change the initial pose.
For manual triggering, launch with `auto_throw:=false`, then call
`ros2 service call /experiment/throw std_srvs/srv/Trigger '{}'`.
One launch is one trial; restart to reset all state.

## Five-throw experiments and offline replay

```zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --output ros/ball_catching_ws/results/exploration \
  --speeds 1.5 2
```

The bounced court practice uses the same five-throw acceptance rule:

```zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --profile court-bounce --output ros/ball_catching_ws/results/court-practice
```

A court demonstration adds `--profile court-bounce --present` to `run_trial.py`.
The original no-bounce court challenge remains available:

```zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --output ros/ball_catching_ws/results/court-exploration \
  --profile court --speeds 20 40 67.056 --distances 23.77
```

Speeds are launch speeds in m/s; 67.056 m/s is 150 mph as a court exploration bound.
The default gentle sweep tests 1.5 and 2 m/s upward launches from 1.1 m height.
`--distances` specifies launcher x coordinates relative to the robot baseline,
not path length; the gentle baseline is predominantly vertical. The runner tests
five seeded target perturbations per point.
Defaults vary the target by ±2 cm horizontally and ±1 cm vertically, using seeds
42–46. The same targets are reused across points. Host preflight verifies floor
clearance and geometric robot reachability, plus net clearance and the
configured impact sequence for court throws;
dynamically hard
throws remain eligible. A point passes only with **5/5 physical catches**.
Invalid trajectories and infrastructure failures are shown separately from
robot misses. Use `--prepare-only` to inspect geometry without simulations.
An experiment exits 0 when exploration completes, even when points fail; an
infrastructure failure exits 2. There is no optimizer or injected delay.

Open the generated `dashboard.html` directly in a browser. It contains all
assets and data, works offline, and needs no web server. Select a parameter
point and a throw; replay the recorded ball and actual robot/finger movement,
scrub, slow playback, or switch between court and robot views. The physical ball
retains its real size; a cyan locator helps visibility. Gold marks its recorded
path. Replay interpolation and mesh reduction affect display only.

Regenerate or combine reports without rerunning physics:

```zsh
python3 ros/ball_catching_ws/scripts/report.py \
  ros/ball_catching_ws/results/exploration \
  --output ros/ball_catching_ws/results/exploration/dashboard.html
```

The overview shows flight time, pre-impact/target-plane arrival speed,
first usable visual track, visual reaction window, first plan's time remaining,
and per-stage 95th-percentile computation cost. A substantial velocity jump
before fingertip contact identifies an inferred earlier impact, such as palm
contact; those arrival measurements are labeled explicitly. Flight ends at stable
capture or
floor contact; a timeout without a known endpoint is marked unavailable. The
visual reaction window ends at stable capture for successes and nominal target
plane crossing for misses (x for court throws, z for overhead tosses). A late
first track can produce a negative window.
For bounced throws, ground impulses are excluded from inferred grasp-arrival
speed. The headline window starts at the usable track after the final bounce
and last pre-capture filter reset; the overview also shows the full first-track
window. Bounce counts/times are shown with the replay.
Processing durations are **native wall time**; physical windows use **simulation
time**. Repeated computation updates are not added together or subtracted from
flight duration. These measurements describe available time, not a validated
hard real-time deadline or guaranteed catch probability. Timing excludes writing
the timing record itself. Future Chimaera runs need guest/simulation clock
accounting; this version preserves records and robot/host interfaces for that work.

## Components and interfaces

`ball_catching_robot` runs four C++ processes: `perception`, `intercept`,
`effort_control`, and `gripper_control` (the last is omitted for the cup).
Eigen/KDL implement dynamics, quintic trajectories, IK and filtering; OpenCV
implements color/contour detection and triangulation. The gripper filter estimates
position, velocity and quadratic drag from images. The planner considers incoming
orientation, closure time and bounded approach/braking trajectories. The host
applies bounded torques/forces and publishes motor feedback; it contains no
trajectory follower. Commands expire after 100 simulated milliseconds.

| Interface | Message | Meaning |
| --- | --- | --- |
| `/stereo/{left,right}/image_raw` | `sensor_msgs/Image` | Synchronized RGB images |
| `/clock` | `rosgraph_msgs/Clock` | Simulation time |
| `/robot/joint_states` | `sensor_msgs/JointState` | Arm/finger positions and velocities, 250 Hz |
| `/robot/ball_state` | `nav_msgs/Odometry` | Visual position/velocity; capture timestamp |
| `/robot/ball_geometry` | `geometry_msgs/Vector3Stamped` | Inferred radius (`x`, m) and drag factor (`y`, 1/m) |
| `/robot/joint_trajectory` | `trajectory_msgs/JointTrajectory` | Timed endpoints, including approach/braking |
| `/robot/effort_command` | `sensor_msgs/JointState` | Seven named arm torques, Nm |
| `/robot/gripper_target` | `geometry_msgs/Vector3Stamped` | Closure time, target width and inferred object width |
| `/robot/gripper_effort` | `sensor_msgs/JointState` | Two named finger forces, N |
| `/robot/gripper_state` | `geometry_msgs/Vector3Stamped` | Width and simulated hardware-observable grasp status |
| `/robot/{ready,perception_ready,gripper_ready}` | `std_msgs/Bool` | Readiness sent to trial management |
| `/evaluation/ball_pose` | `geometry_msgs/PoseStamped` | Privileged evaluation; robot never subscribes |
| `/evaluation/result` | `std_msgs/String` | Durable JSON score; robot never subscribes |
| `/experiment/throw` | `std_srvs/Trigger` | Host-only launch request |

Stereo calibration is robot configuration. Parallel cameras look along world
+x, with optical right=-y and down=-z. Bounded image queues discard older or
unmatched images. Grasp feedback uses measured finger width and convergence;
it never checks true ball identity or position. Passive scoring independently
requires both rubber tips to contact the ball, low relative ball/hand speed,
location between fingers, and uninterrupted retention without an unpermitted
floor contact. The bounced profile records permitted receiving-half impacts
explicitly; a hardware grasp report alone cannot qualify a catch.

All robot computation belongs in a future guest. Physics, camera rendering,
experiment generation and passive evaluation remain host-side. Image transport
capacity and gem5 synchronization are future integration work.

## Records and checks

Each trial preserves configuration, invocation/source provenance (including
source and installed-native-artifact hashes), generated
world/URDF, `ground_truth.csv`, robot JSONL records, `result.json`, `summary.json`,
`bounces.jsonl` for court impacts, and launch/runtime logs. Experiments add an incremental manifest and throw seeds.
Ground truth records actual arm/finger positions, ball velocity and bilateral
contacts for evaluation/replay only. Results live under the ignored `results/`
directory; copy a dashboard to share it.

```zsh
colcon --log-base ros/ball_catching_ws/log test \
  --build-base ros/ball_catching_ws/build --install-base ros/ball_catching_ws/install \
  --packages-select ball_catching_robot ball_catching_sim
colcon --log-base ros/ball_catching_ws/log test-result \
  --test-result-base ros/ball_catching_ws/build --verbose
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONPATH=ros/ball_catching_ws/src/ball_catching_sim:$PYTHONPATH \
  OPENBLAS_NUM_THREADS=1 python3 -m pytest -q ros/ball_catching_ws/tests
```

FR3 position, torque and nominal speed limits come from the pinned description.
Acceleration and position-dependent speed bounds follow the
[FR3 interface specifications](https://frankarobotics.github.io/docs/robot_specifications.html).
Trajectory feasibility is sampled. The finger servo caps requested travel speed,
and the physics adapter configures joint velocity bounds. DART collision
impulses can still produce transient finger speed/position overshoot; the model
does not guarantee that collision disturbances respect all motion limits.
Independent simulated finger actuators approximate the commercial hand
coupling. These are remaining hardware-model limitations. Contact and aerodynamics are simplified assumptions,
not a calibrated tennis ball. There is no general obstacle/self-collision planner.
See [grasping requirements](../../docs/ball-grasping-design.md),
[grasping validation](../../docs/ball-grasping-validation.md), and
[previous cup validation](../../docs/ball-catching-validation.md).
