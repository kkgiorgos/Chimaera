# Native FR3 visual ball catching

ROS 2 Humble / Gazebo Fortress application using the upstream open-source FR3
description, fixed stereo RGB cameras, classical color/contour detection,
triangulation, gravity-aware state estimation, continually revised interception,
and computed-torque joint feedback control. The cup stays upward and has a rigid
offset bracket to keep its opening clear of the wrist.

## Build and run

Run from the repository root with native ROS/Gazebo and Python OpenCV, NumPy,
PyKDL, and pytest installed. The existing FR3 description dependency is
`ros/franka_ws/src/franka_description` (version 1.6.1). No libfranka build, MoveIt,
Docker, or guest is required.

```zsh
source /opt/ros/humble/setup.zsh
colcon --log-base ros/ball_catching_ws/log build \
  --base-paths ros/franka_ws/src/franka_description ros/ball_catching_ws/src \
  --build-base ros/ball_catching_ws/build \
  --install-base ros/ball_catching_ws/install \
  --cmake-args -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release
source ros/ball_catching_ws/install/local_setup.zsh
ros2 launch ball_catching_sim application.launch.py
```

Use `.bash` setup files in bash. Gazebo GUI is on by default. A throw starts once
the arm is settled and synchronized camera images are arriving. No launch event
or throw parameters are sent to the robot nodes.

Run a headless trial with automatic shutdown and retained results:

```zsh
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/demo
```

The default throw is from `[2.08, 0.04, 2.0]` metres, along `[-1, 0, 0]`, at
3 m/s. The runner isolates Gazebo transport and ROS discovery, writes launch
logs, and stops the application when scoring finishes. Exit status is 0 for a
catch and 1 for a miss; infrastructure errors produce a traceback and logs.
Use a fresh output directory for every trial. The runner wall timeout defaults
to 120 seconds; it includes startup and depends on rendering performance.

```zsh
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/custom \
  --position 2.08 0.04 2.0 --direction -1 0.05 0.1 --speed 3.0 \
  --retention 1.0 --camera-hz 90
```

Launch arguments also expose `initial_pose` (seven joint angles, JSON array),
`launch_position`, `launch_direction`, `launch_speed`, `retention`, `camera_hz`,
`camera_width`, `camera_height`, `physics_step`, `cup_radius`, `cup_depth`, `trial_timeout`, `auto_throw`, `gui`,
`robot`, `output`, and `partition`. Direction is normalized; speed is its magnitude.
The cup defaults to a 0.12 m inner wall radius and 0.14 m depth. Ball radius is
0.0335 m and mass 0.057 kg in the simulator; the robot infers ball radius from
stereo detections and receives neither value.

For manual throws use `auto_throw:=false`, then:

```zsh
ros2 service call /experiment/throw std_srvs/srv/Trigger '{}'
```

One launch is one trial. Restart to reset the robot, ball, filter, and scorer.
Custom starting poses are checked against joint limits and must preserve the
upward cup orientation. The runner also accepts `--initial-pose` and
`--resolution WIDTH HEIGHT`.

## Components and interfaces

`ball_catching_robot` contains three independently executable ROS processes:
`perception`, `intercept`, and `effort_control`. `robot.launch.py` starts robot
compute alone with a URDF path, initial pose, and stereo calibration. It accepts
no experiment settings. `ball_catching_sim` generates the world and supplies a
Fortress system plugin for actuation, trial lifecycle, and passive scoring.

| Interface | Message | Meaning |
| --- | --- | --- |
| `/stereo/{left,right}/image_raw` | `sensor_msgs/Image` | Synchronized RGB/BGR images |
| `/clock` | `rosgraph_msgs/Clock` | Simulation time |
| `/robot/joint_states` | `sensor_msgs/JointState` | Named joint position/velocity feedback, 250 Hz |
| `/robot/ball_state` | `nav_msgs/Odometry` | Visual world-frame position/velocity estimate; stamp is capture time |
| `/robot/ball_geometry` | `geometry_msgs/Vector3Stamped` | Visually inferred radius in `vector.x` metres; y/z reserved |
| `/robot/joint_trajectory` | `trajectory_msgs/JointTrajectory` | Two timed endpoints with position, velocity, acceleration |
| `/robot/effort_command` | `sensor_msgs/JointState` | Seven ordered named torque commands in `effort`, Nm; sim-time stamp |
| `/robot/{ready,perception_ready}` | `std_msgs/Bool` | Readiness sent outward to trial management |
| `/evaluation/ball_pose` | `geometry_msgs/PoseStamped` | Privileged scoring/debug output; robot never subscribes |
| `/evaluation/result` | `std_msgs/String` | Durable JSON catch result; robot never subscribes |
| `/experiment/throw` | `std_srvs/Trigger` | Host-only trial launch request |

Stereo calibration is explicit robot configuration: focal length, principal
point, baseline, and left camera origin in the world/base frame. Parallel cameras
look along world +x with optical right=-y and down=-z. Images must have identical
capture timestamps. Perception uses bounded queues and discards older/unmatched
images rather than accumulating delay. No simulator ball poses enter perception,
estimation, IK, planning, or control.

All three robot processes, including computed-torque feedback, belong in a
future guest. The host applies bounded motor torques and publishes feedback;
it contains no trajectory follower. Stale commands expire after 100 simulated
milliseconds. The image bridge and physics clock remain host-side. Image
transport capacity and gem5 synchronization are future integration work.

## Records, checks, and current scope

Each recorded trial keeps `experiment.json`, `metadata.json` (source revision,
working-tree status, invocation, ROS domain and wall duration), generated `world.sdf`/`robot.urdf`,
`ground_truth.csv`, `perception.jsonl`, `interception.jsonl`, `control.jsonl`,
`result.json`, and launch/runtime logs. Capture times, observation age, planning
wall duration, target revisions, desired/actual joints, and effort commands help
explain catches and misses. Scoring requires the complete ball to remain within
the cup for a continuous configurable duration (default 1.0 simulated second).
Entry alone is insufficient; bounce-out resets the retention timer. Timeout is
four simulated seconds after launch by default.
Containment uses the actual polygonal wall geometry with a 2 mm contact-solver
tolerance. Ground truth records the cup orientation as well as position.

```zsh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONPATH=ros/ball_catching_ws/src/ball_catching_robot:ros/ball_catching_ws/src/ball_catching_sim:$PYTHONPATH \
  OPENBLAS_NUM_THREADS=1 python3 -m pytest -q ros/ball_catching_ws/tests

# Enable/build the native scorer test, then run it:
colcon --log-base ros/ball_catching_ws/log build \
  --base-paths ros/ball_catching_ws/src \
  --build-base ros/ball_catching_ws/build --install-base ros/ball_catching_ws/install \
  --cmake-args -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
colcon --log-base ros/ball_catching_ws/log test \
  --build-base ros/ball_catching_ws/build --install-base ros/ball_catching_ws/install \
  --packages-select ball_catching_sim
```

The initial implementation demonstrates physical retention and visual feedback,
but does not establish a maximum catch speed or guaranteed success for every
geometrically reachable throw. Camera visibility, sensing latency, motion limits,
and entry direction constrain the envelope. Detection assumes a distinctive
yellow/green ball in a simple scene. Interception considers descending flight
through several candidate heights and finishes at zero cup velocity; velocity
matching remains future work if contact behavior requires it. There is no
general-purpose obstacle or self-collision planner yet.

FR3 position, nominal speed, and torque limits come from the pinned description.
Acceleration and position-dependent speed bounds follow the
[FR3 interface specifications](https://frankarobotics.github.io/docs/robot_specifications.html).
Quintic trajectory feasibility is sampled along the path. Physics enforces
actuator effort limits; planning limits are not a claim that every disturbed
physical trajectory remains within all bounds. Contact parameters are simplified
simulation assumptions, not a calibrated tennis-ball material model.

See [native validation](../../docs/ball-catching-validation.md) for tested
directions, speeds, and the limits of those results.
