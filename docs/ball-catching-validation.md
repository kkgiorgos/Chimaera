# Native FR3 catching validation

Validated on 2026-10-05 with ROS 2 Humble, Gazebo Fortress 6.18.0, and the existing
Franka description 1.6.1. The table below records the C++ rewrite's regression
trials, using C++17/rclcpp, Eigen/KDL, and native OpenCV 4.5.4.
All trials used actual rendered stereo images and
effort-controlled physics. The robot received calibration and arm/cup geometry,
but no launch parameters, ball ground truth, scoring results, or launch event.
Ball radius was inferred visually. No attachment or artificial capture was used.

## Native trials

All cases used a 0.12 m cup wall radius, 0.14 m cup depth, 640×480 stereo images
at 90 Hz, 1 ms physics steps, and 1.0 simulated second of continuous retention.
Directions below are normalized by the launcher. Position is in metres and
speed in m/s. Trials ran sequentially with isolated Gazebo partitions/ROS domains.

| Case | Launch position | Direction | Speed | Starting pose | Outcome | Seconds after launch |
| --- | --- | --- | --- | --- | --- | --- |
| Default | `[2.08, 0.04, 2.0]` | `[-1, 0, 0]` | 3.0 | Neutral | Caught | 1.512 |
| Default repeat | `[2.08, 0.04, 2.0]` | `[-1, 0, 0]` | 3.0 | Neutral | Caught | 1.512 |
| Diagonal | `[2.08, -0.11, 2.0]` | `[-1, 0.1, 0]` | 3.0 | Neutral | Caught | 1.516 |
| Faster flight | `[3.05, 0.04, 2.0]` | `[-1, 0, 0]` | 5.0 | Neutral | Caught | 1.516 |
| Inclined/custom pose | `[2.18, 0.10, 2.0]` | `[-1, 0, 0.05]` | 3.2 | Neutral with joint 1 = 0.1 rad | Caught | 1.532 |
| Intentional miss | `[2.08, 0.04, 2.0]` | `[-1, 0, 0]` | 10.0 | Neutral | Miss at timeout | 4.000 |

These outcomes match the six original Python validation cases. The successful
C++ cases produced one to four visual plans before interception, including
continuous revisions in the repeat, diagonal, faster, and custom-pose cases.
Pre-impact visual position RMSE was approximately 0.6–1.5 cm,
comparing filtered estimates at capture timestamps against interpolated ground
truth during the first 0.4 seconds of flight. This short-window measurement is
a diagnostic, not a benchmark of general perception accuracy.

The desired cup orientation remained upward. Physical orientation error peaked
at approximately 2.8–4.1 degrees during successful trials, including impact;
effort control does not impose an ideal rigid orientation constraint.

These few trials establish a working native demonstration, not a measured
maximum speed, success probability, or exhaustive catching envelope. The 5 m/s
case uses a different launch position from the 3 m/s cases. It must not be
interpreted as a fixed-vector speed sweep. Contact properties are simplified
assumptions and camera noise/occlusion are not systematically varied.

## Automated checks and integration fixes

- 13 native C++ algorithm tests passed: metric stereo reconstruction and color
  detection without known ball size, ballistic velocity recovery, stale/outlier
  handling, motion continuity and feasibility, FR3 FK/IK, positive inertia,
  gravity versus independent potential-energy derivatives including bracket
  inertia, and invalid model/starting-pose rejection.
- Numerical regression tests compare FK, rotation, mass matrices, gravity/
  Coriolis forces, noisy filter states, and quintic position/velocity/acceleration
  with outputs saved from Python revision `3ca3904`. Matching tolerances are
  1e-9 or tighter. The reference fixture uses the pinned FR3 description and
  default cup/bracket geometry; it is test data, with no Python robot runtime.
- 10 Python scene-configuration rejection tests passed, invoking the compiled
  arm validator when checking joint limits and cup orientation.
- The native C++ scorer test passed: polygon-corner containment, wall/rim/bottom
  rejection, and resetting the retention timer after escape.
- Native builds passed. All robot processes exited cleanly in every trial.
  Gazebo required ROS launch's SIGTERM fallback after scoring in the first
  default case; all six application processes exited cleanly on SIGINT in the
  other five cases. `git diff --check` passed.

Integration testing exposed and resolved wrist feedback instability, persistent
Fortress launch commands preventing free ballistic flight, wrist obstruction of
the cup opening, incorrect circular scoring of polygon-wall contact, cross-trial
ROS discovery, and duplicate shutdown signals. Earlier failed debug attempts
were kept outside the final validation set.

## Reproduce and inspect

These are archived results from the earlier bent cup home. The current cup
demonstration uses vertical home, speed matching and braking; see
[the updated cup example](ball-cup-home-braking.md). The old flat launches remain
available as challenges, but historical outcomes do not qualify the new start.

Follow [the native workspace instructions](../ros/ball_catching_ws/README.md).
The C++ trial records on this development machine are under
`ros/ball_catching_ws/results/cpp-{default,repeat,diagonal,fast,pose,miss}`.
The original Python records remain under
`ros/ball_catching_ws/results/validation-{default,repeat,diagonal,fast,pose,miss}`.
Generated worlds, robot descriptions, raw trajectories, estimates, commands,
scores, and logs are retained there. Results/build artifacts are excluded from
Git; this report and all source/tests are committed.

For example, after building and sourcing the overlay:

```zsh
python3 ros/ball_catching_ws/scripts/run_trial.py --output /tmp/fr3-default
python3 ros/ball_catching_ws/scripts/run_trial.py --output /tmp/fr3-diagonal \
  --position 2.08 -0.11 2.0 --direction -1 0.1 0 --speed 3
python3 ros/ball_catching_ws/scripts/run_trial.py --output /tmp/fr3-fast \
  --position 3.05 0.04 2.0 --speed 5
python3 ros/ball_catching_ws/scripts/run_trial.py --output /tmp/fr3-miss --speed 10
```

The miss command intentionally returns status 1. Use new output directories
when repeating any command.

## Source line comparison

Compared with Python revision `3ca3904`, robot runtime source grew from **615
to 722 physical lines**, an increase of **107 lines (17.4%)**. This counts
comments and blank lines in the former seven Python modules and the new C++
runtime source plus shared headers. Launch files, tests/reference data, package
metadata, and build files are excluded. The additional standalone starting-pose
validator is 17 lines; including that utility gives **739 C++ lines**, an
increase of **124 (20.2%)** over the former runtime.

ROS topic contracts, simulation-time scheduling, and the future host/guest
boundary are preserved. Launch and experiment tooling remain Python. The
rewrite removes the robot's rclpy, Python OpenCV, NumPy, and PyKDL dependencies;
simulation tooling still uses Python NumPy/xacro.
