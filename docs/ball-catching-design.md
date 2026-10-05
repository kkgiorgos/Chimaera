# FR3 ball-catching design

Status: native implementation demonstrated with visual catches at 3–5 m/s;
operating-envelope characterization and gem5 integration remain future work.
See [implementation instructions](../ros/ball_catching_ws/README.md) and
[native validation](ball-catching-validation.md).

## Objective and accepted requirements

Demonstrate classical visual ball catching with ROS 2 Humble and Gazebo Fortress
on the native development machine. Use an FR3 with a rigid custom cup, a fixed
upward cup orientation, and fixed stereo cameras. Actual hardware deployment is
out of scope. Containers and gem5 integration are future work.

The eventual research outcome is catch success versus computer architecture,
including the maximum successful launch speed for a fixed launch position and
direction. The launch configuration specifies position, direction, and scalar
speed. The system should cover diverse throws within its effective catching
envelope, rather than specialize to a scripted trajectory. Geometric reach alone
does not guarantee a catch: observation time, joint dynamics, orientation,
visibility, and entry direction also constrain feasibility.

Model a tennis ball with gravity during flight and physical contact at impact;
omit aerodynamic drag and spin effects initially. Use a simple scene and
visually distinctive ball. The robot may know gravity; ball position, velocity,
launch time, and size must not be supplied from experiment configuration or
simulator ground truth. Known equipment configuration includes camera
intrinsics, stereo baseline/extrinsics, camera-to-base transforms, and FR3/cup
geometry. These quantities are part of robot calibration, not privileged ball
state.

Continually revise the interception target from visual observations. Keep the
cup orientation fixed upward in world coordinates while moving its position;
coordinate wrist motion to preserve that orientation despite the rigid
attachment. Investigate vertical velocity
matching if contact tests show excessive bounce. Success requires physical entry
and retention for one simulated second by default, with a configurable duration.
Every trial starts from a configurable joint pose, using the same neutral pose
by default for comparable speed sweeps. The robot detects the throw visually;
it receives no launch notification. Cup dimensions, contact parameters, camera
settings and operating envelope remain experiment variables. The implemented
cup uses a rigid offset bracket to clear the wrist, radius 0.12 m and depth
0.14 m. The default neutral pose is `[0, -pi/4, 0, -3*pi/4, 0, pi/2, pi/4]`.

## Proposed functional pipeline

1. Acquire synchronized, timestamped left/right RGB images and camera metadata.
2. Segment the ball using classical color and contour processing; associate
   detections using stereo geometry and temporal prediction.
3. Triangulate the center and estimate observation uncertainty. Infer apparent
   ball size from image geometry if needed; never read the simulator ball pose.
4. Estimate position and velocity with a gravity-aware classical filter;
   propagate uncertainty, reject outliers, and handle missing detections.
5. Predict the ballistic trajectory from observation timestamps to future
   interception times. Account for image age and command delay.
6. Select a reachable interception position/time under fixed orientation and
   joint motion limits. Replan while preserving command continuity.
7. Track continuously revised, time-constrained joint trajectories with an
   effort-based feedback controller. Enforce joint position, velocity,
   acceleration, and torque limits; avoid discontinuities at target revisions.
   Native control uses KDL model-based gravity/Coriolis compensation and
   inertia-scaled feedback; numerical trajectory checks enforce motion limits.

No active stage may consume throw settings, scoring data, privileged poses, or
simulator-only state. Diagnostic observations can be recorded for offline
comparison without feeding back into decisions.

## Local organization and future host/guest boundary

Follow the wall-follow pattern: independently launchable robot application,
simulation adapter, and passive scorer/recorder. A local composition launch
starts them together without introducing a guest or transport dependency.
Application code must depend on ROS sensor and actuator contracts rather than
Gazebo APIs. Use stamped data and explicit simulation-time behavior from the
beginning. Separate ROS adapters from algorithm code.

The agreed future guest includes vision, filtering, prediction, IK, motion
generation, and effort-feedback control. Physics, rendering, launch generation,
scoring, and motor actuation belong on the host. Host actuation must apply
received torque commands and expose joint feedback without secretly performing
the application's trajectory tracking. Local control should use the same
sensor/actuator boundary so guest integration does not require moving controller
logic out of a Gazebo-specific plugin.

Candidate boundary data: stereo images, CameraInfo, joint feedback, time,
static calibration/robot configuration, and actuator commands. Topic names,
message types, QoS, transport capacity, and deadline behavior are not finalized.
Image bandwidth and synchronization must be evaluated before gem5 integration;
the wall-follow lidar workload does not establish stereo transport performance.

## Evaluation and reproducibility

The host scorer may access all simulator state. Require physical retention,
not a synthetic attachment or success on first contact. Distinguish catch/miss
from infrastructure failure, and retain failure reasons such as absent visual
track, infeasible intercept, late arrival, rim collision, or bounce-out when
those can be established. Record primary binary outcomes and timestamped
observations, estimates, target revisions, commands, joint feedback, and scorer
state to explain failures offline.

Record launch position/direction/speed, initial robot pose, sensor configuration,
physics/contact parameters, seeds, and software provenance. For a fixed vector,
sample success versus speed and repeat trials where variability exists. Do not
assume success is strictly monotonic with speed or that a single trial defines
a reliable maximum. Retention uses simulated time and defaults to 1.0 seconds;
record the configured retention duration with each trial. Any shorter future
benchmark criterion must be validated against a longer observation period.
Reset and settle the arm at the configured starting pose before each throw;
ensure capture and application readiness before launching the ball without
exposing that launch event to the robot pipeline. Keep the starting pose fixed
within a speed comparison unless pose is an explicit experiment variable.

## Implementation milestones

1. Validate the existing FR3 sources and dependencies against native
   Humble/Fortress and choose the effort-feedback controller implementation.
2. Bring up FR3, a collision-capable cup, stereo cameras, and configurable throws;
   verify physical retention/contact and a separate ground-truth scorer.
3. Implement and validate stereo detection, reconstruction, and ballistic
   tracking against offline ground truth.
4. Implement continuously revised interception and physically limited tracking;
   demonstrate catches over several directions and speeds.
5. Characterize the catching envelope and save repeatable trials with timing
   diagnostics. Integrate gem5 and later container orchestration separately.

Work stays on `feat/ball-catching`, with scoped conventional commits at
significant milestones.

## References

- [Humble gz_ros2_control](https://control.ros.org/humble/doc/gz_ros2_control/doc/index.html)
- [Humble joint trajectory controller parameters](https://control.ros.org/humble/doc/ros2_controllers/joint_trajectory_controller/doc/parameters.html)
- [FR3 interface limits](https://support.franka.de/docs/control_parameters.html)
- Local `ros/wall_follow_ws/README.md` and `ros/franka_ws/src/franka_gazebo_bringup`.
