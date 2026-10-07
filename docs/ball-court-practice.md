# Full-court practice catching

The `court-bounce` profile is a deliberately slow practice lob across the full
23.77 m court and its physical net. The robot remains at the receiving baseline
and catches the ball in the air after **two permitted ground bounces**. This is
a robot-catching benchmark, not a legal tennis return or a fast-serve result.
The original no-bounce `court` challenge and the nearby `gentle` profile remain
available. Every profile uses the stock hand, finite contact friction and bounded
motor commands; there is no ball attachment or attraction to the hand.

## Run and change the setup

Build/source the workspace as described in `ros/ball_catching_ws/README.md`, then:

```zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --profile court-bounce --output ros/ball_catching_ws/results/my-court-run

python3 ros/ball_catching_ws/scripts/run_trial.py \
  --profile court-bounce --present --retention 1 \
  --output ros/ball_catching_ws/results/my-court-demo
```

The upright-start court point passed **5/5** in
`results/court-upright-current-pose-validation`; its dashboard replays the whole
approach. See [measured validation and prior failures](ball-grasping-validation.md).

Output directories must be new or empty. Experiments run five seeded directions
and require five physical catches. They preserve failed trials. Presentation is
optional and omitted by the experiment runner. Every mode and profile starts
from fixed upright home: `[0°, −45°, 0°, −135°, 0°, 90°, 45°]`. Custom initial
poses are rejected, including direct scene/launch overrides. The ball launches
after readiness; the arm remains at home until a visual observation triggers
robot-side planning.

| Choice | Default | How to change it |
| --- | --- | --- |
| Launch magnitude | 14 m/s (31.3 mph) | `--speeds` in experiments; `--speed` in single trials |
| Launcher | `[23.77, 0, 2.5]` m | `--distances`, `--launch-height`; single-trial `--position` |
| Catch target | `[0.5, 0, 0.8]` m | `--target X Y Z` |
| Direction variation | Seeds 42–46; ±2 cm in x/y, ±1 cm in z | `--seed`, `--target-jitter` in experiments |
| Allowed bounces | 2 | `--bounces 0..4`; zero uses the no-bounce trajectory solver |
| Arc | Lowest valid sampled arc | `--arc low\|high` |
| Normal court restitution | 0.745 | `--court-restitution` |
| Horizontal speed ratio at impact | 0.6 | `--court-tangent-ratio` |
| Starting arm pose | Fixed upright home | Not configurable per trial |
| Preflight target-axis elevation | 30° above horizontal | `--target-pitch` (`--home-pitch` legacy alias); validates reachability only |
| Stereo pair centre | `[-1, 0.6, 1.2]` m | `--camera-position X Y Z` |
| Stereo baseline | 0.30 m | `scene.py`; generated calibration follows the scene |
| Image dimensions / rate | 1280×960 / 90 Hz | `--resolution WIDTH HEIGHT`, `--camera-hz` |
| Motor startup compensation | 0.025 s of earlier closure | `--gripper-lead`; zero disables compensation |
| Continuous physical retention | 0.2 s | Single-trial `--retention` |
| Ball / finger friction | 1.0 | Single-trial `--grip-friction` |

Ball diameter remains 67 mm and mass 57 g. The stock finger geometry and rubber
tips are unchanged. The hand commands at most 50 mm/s per finger; total grasp
force is conservatively split as 25 N per finger. Arm motion, position-dependent
joint speed, acceleration and torque limits remain active. The existing
Gazebo/DART impact-driven joint-overshoot limitation still applies; this is not
a calibrated hardware certification.

## Why these choices

Lower launch speed alone cannot make the previous no-bounce full-court setup
gentle: flight must still cover the court against gravity and drag. The
no-bounce 18 m/s case arrives around 13 m/s. A preflight single-bounce 16 m/s
practice shot arrives around 6.8 m/s. The two-bounce 14 m/s shot arrives around
3.1 m/s, within the regime established by nearby gentle catches. Choosing two
bounces trades tennis-return fidelity for a repeatable full-court benchmark.
Higher speeds and one bounce remain configurable challenges, without a promise
that the current robot/controller can catch them.

The original stereo pair, centred behind the arm, obscured the second rebound.
Moving it sideways gives an unobstructed view; calibration is regenerated from
the actual camera pose. At 640×480, small centroid/disparity errors produced
large short-track velocity errors. Doubling each image dimension improved
interception placement, at the cost of more perception work. This remains a
real camera input, not an exact simulated position measurement.

The flight filter now discards its continuous-flight state when consecutive
visual observations show an upward reversal near the known floor while its
estimated velocity is downward. It reacquires from new images rather than
forcing the old filter to explain an impact as large drag. The robot receives
no bounce count, restitution, contact event, launch direction or launch speed.
The filter's existing long-gap/outlier reset remains available. A fresh track
also clears its old covariance, preventing correlations from the previous flight
from contaminating the rebound estimate.

Stereo measurement covariance now follows the calibrated projection Jacobian,
including the correlation between depth and lateral/vertical position. The
pixel-centroid standard deviation is 0.35 px, with a 2 mm position-noise floor
(`ball_catching_robot/src/vision.cpp`, `Stereo::observationNoise`). This is an
illustrative sensor-noise setting, not a camera calibration certificate. The
planner rejects candidates whose projected aggregate position standard
deviation exceeds 60 mm (`src/intercept.cpp`, `planGrasp`). These are robot-side
uncertainty checks rather than injected delays. Replace/tune the noise model
and this threshold when using measured camera noise.

An interception that expires without hardware grasp feedback is released after
150 ms; the hand can reopen and plan another visually observed flight. Earlier
code locked permanently after an expired attempt, allowing a noisy distant
prediction to prevent every later catch. The 150 ms retry grace period is in
`planGrasp`; it does not delay an initial catch or extend ball flight.

The arm now starts from the fixed upright home, rather than a catching pose.
When a fresh visual track shows a distant ball (`x > 1.5 m`) moving toward the
robot (`vx < −0.5 m/s`), the robot plans a coarse approach toward its nominal
workspace centre: 0.5 m along the observed horizontal bearing, at 0.8 m height,
with the hand axis elevated 30°. This centre/orientation is a fixed robot-side
workspace prior, not the experiment's per-throw target or a predicted exact
catch. The approach is triggered by camera estimates, not launch notification.
It searches 27 IK seeds for joint margin and a feasible 0.8–2.4 s bounded quintic
trajectory; fingers stay open. Precise interception still uses the visual flight
estimate and uncertainty gate and may refine the approach during flight. No
launch speed, ground-truth position, bounce event or future trajectory is given
to the robot. Approach planning cost is included in the planning measurements;
its trigger time and commanded duration appear in the overview. Interception
uses the current commanded hand height/orientation after approach, rather than
keeping the original upright home as its waiting-pose reference.

Historical prepared-pose records such as `court-practice-release` and
`court-practice-presentation-release` remain unchanged and do not qualify the
upright-start version. They needed only 5.8–24.9 mm hand displacement through
capture. Current dashboards explicitly label that historical starting condition.
Replay starts at launch and includes the whole approach. The dashboard's hand
position and displacement use the recorded tool-frame origin, which differs
from the ball-centre grasp target. Displacement is an endpoint distance through
flight end, excluding presentation; it is not total path length.

Perception continuously updates position, velocity and drag on each usable
stereo pair (nominal 90 Hz). The gripper planner is scheduled at 30 Hz with a
50 ms minimum interval, so normal catch refinements occur roughly every 67 ms.
A feasible replacement updates the arm trajectory and closure target; a failed
search leaves the previous plan active. Refinement stops in the final 25 ms
before the currently committed catch, while visual estimation continues.
Once closure has started, a later timing refinement cannot reopen the fingers.
The coarse visual approach is planned once; precise interception is repeatedly
estimated and replanned. Expired attempts can retry as described above.

Upright-start trials also revealed a low-inertia wrist-joint tracking offset:
the computed-torque PD controller could stop short when its torque correction
fell below static friction. The controller now integrates position error into
a bounded torque correction (gain 4 N·m/(rad·s), clamp ±1 N·m), freezes outward
integration at actuator saturation, and still applies the original torque and
speed limits. `control.jsonl` records the correction as `integral_torque`.
This changes the servo algorithm rather than removing physical joint friction.

The 25 ms motor lead starts closure earlier to compensate the observed servo
startup/tracking lag; it introduces no wait or artificial flight extension.
Once closure starts, later plan refinements cannot temporarily reopen the
fingers merely by supplying a later closure timestamp. Grasp confirmation uses
opening velocity (the sum of finger velocities), width and motor stall. In the
independent-finger approximation, opposite finger motions can leave total width
stable; requiring both individual velocities to be zero falsely rejected a
physically held ball and caused the retry path to open the hand. The corrected
feedback remains hardware-observable and does not inspect contact identities.
After its initial 20 ms stall confirmation, grasp status stays active while
measured width remains within the object tolerance; brief loaded width motion
during wrist presentation does not reopen a held ball. A genuinely empty hand
still fails the width test.

Presentation waits for the requested physical hold interval plus 50 ms from
hardware confirmation (at least 250 ms). Thus wrist motion cannot reset a longer
retention test or change catch timing. This is an optional task requirement, not
an injected delay in benchmark flight. `presentation_done.json` independently
records whether the ball is still physically held at the end. A failed gesture
leaves the catch score intact, but the demonstration runner returns failure and
the dashboard reports the lost ball.

## Court impact model and its limits

The court uses an explicit, passive, empirical impact law on the host:

```
vx_after = 0.6 * vx_before
vy_after = 0.6 * vy_before
vz_after = -0.745 * vz_before
```

Both coefficients are restricted to `(0, 1]`; the law dissipates translational
kinetic energy. Normal restitution 0.745 corresponds to the standard tennis-ball
drop-test range reported by Cross. His measurements also show horizontal speed
ratios varying with court, incidence and spin, including ratios below the rigid
rolling value. A constant 0.6 is an illustrative court parameter, not a fitted
surface identification. See [Cross, measured court-impact study](https://www.physics.sydney.edu.au/~cross/PUBLICATIONS/23.%20CourtSpeed.PDF).

The model omits explicit ball deformation and spin exchange. Consequently, it
cannot establish real-court pace, predict all repeated-bounce spin effects, or
certify fast serves. Adjust these coefficients or replace the law with a
calibrated compliant/spinning-ball model when those effects become the subject
of the experiment. Air drag remains quadratic, with host Cd=0.55; robot drag
estimation is independent.

The Gazebo host detects a descending floor-plane crossing within the next
physics step and applies the impact velocity once. It preserves ball position,
then returns to free native flight and drag. Event timing is resolved to the
1 ms physics step. This pair-specific law avoids conflating the court response
with the native ball/finger restitution; robot and net collisions remain native
Gazebo contacts. No privileged ball motion is used in robot control.

Only receiving-half impacts within the court bounds qualify. A subsequent
unpermitted ground contact fails the trial. Ground contact is neither globally
ignored nor renamed a successful grasp. Physical success still requires both
stock rubber tips to contact the ball, between-finger containment, low relative
speed and continuous retention. The actual bounce count is recorded.

## Records and interpretation

`experiment.json` stores launch, material, camera, hand-pose and timing settings;
`metadata.json` stores the invocation, source revision/dirty status, working-tree
implementation fingerprint and installed-native-artifact hashes.
`bounces.jsonl` records host-only impact positions, times and incoming/outgoing
velocities. `ground_truth.csv` includes actual arm/finger/ball motion and bounce
count. `perception.jsonl` records visual state, estimated drag and track resets.
These records are evaluation artifacts and are not controller inputs.

The offline dashboard includes the full path and ground-impact markers, actual
robot/finger replay, measured incoming grasp speed and per-stage compute costs.
Ground impulses are excluded from grasp-arrival speed inference. For bounced
throws, the headline visual window uses the usable track after the final bounce
and its last pre-capture reset; the overview also preserves the first-track
window for the whole flight. This avoids mistaking several seconds of court
travel for several seconds of stable final-flight prediction.

Physical windows use simulation time; computation costs use native wall time.
They do not establish a hard deadline or hardware success probability.
Validation results and preserved development failures are recorded in
[ball-grasping-validation.md](ball-grasping-validation.md).
