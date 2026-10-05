# FR3 ball-catching design

Status: requirements captured; control and calibration decisions pending.

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
simulator ground truth. Camera calibration and robot/cup geometry require a
separate clarification because metric stereo reconstruction and robot control
need those quantities.

Continually revise the interception target from visual observations. Keep the
cup orientation fixed while moving its position. Investigate vertical velocity
matching if contact tests show excessive bounce. Success requires physical entry
and retention, with the retention duration still to be chosen. Cup dimensions,
contact parameters, camera settings, initial arm pose, and operating envelope
remain to be established experimentally.

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
7. Track the resulting joint motion using joint feedback. Controller type and
   actuator command interface are pending.

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

Future guest placement follows the computer being modeled. Vision, filtering,
prediction, IK, motion generation, and any control loop implemented on that
computer belong in the guest. Physics, rendering, launch generation, scoring,
and actuator behavior belong on the host. A loop representing embedded robot
firmware can remain host-side, but that choice must be explicit rather than
silently excluding controller computation from architecture measurements.

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
a reliable maximum. Retention uses simulated time; any shorter future benchmark
criterion must be validated against a longer observation period.

## Implementation milestones

1. Resolve calibration, control fidelity, and guest placement; validate the
   existing FR3 sources and dependencies against native Humble/Fortress.
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
