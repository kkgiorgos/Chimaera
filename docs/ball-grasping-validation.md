# Native airborne-grasping validation

Updated on 2026-10-08; historical trials validated on 2026-10-07 with native ROS 2 Humble / Gazebo Fortress, the pinned
FR3 description, and the working-tree implementation. Trial `metadata.json`
records the source revision, dirty status, invocation and runtime environment.
Raw records and portable dashboards are preserved locally under
`ros/ball_catching_ws/results/` (ignored by Git).

## Fixed upright start (current requirement)

Every profile and mode now starts from `[0°, −45°, 0°, −135°, 0°, 90°, 45°]`.
Scene generation and both launch entry points reject custom starting poses;
`run_trial.py` no longer accepts `--initial-pose`. The robot may move only after
its visual observations trigger a plan. `--target-pitch` affects geometric
preflight reachability, not home orientation.

The qualifying run `court-upright-current-pose-validation` passed **5/5** with
seeds 42–46 and the original ±2 cm horizontal / ±1 cm vertical variation.
It keeps the 14 m/s full-court launch, two permitted impacts, ball friction,
cameras and hardware limits unchanged. All five records share one implementation
fingerprint. Open its `dashboard.html` for launch-from-home replay and timings.

| Measured quantity across five throws | Range |
| --- | --- |
| Incoming grasp speed | 3.080–3.116 m/s |
| Flight to stable capture | 3.232–3.240 s |
| Visual approach trigger after launch | 1.196–1.235 s |
| Commanded coarse approach duration | 1.300 s |
| Hand-frame displacement, launch to capture | 19.5–20.9 cm |
| Usable post-final-bounce visual window | 439–500 ms |
| Per-trial p95 perception/estimation | 14.25–15.29 ms |
| Per-trial p95 planning, including approach | 5.12–7.88 ms |

The configured starting angles are identical in all five trials; recorded launch
joint deviations stayed below 0.018 rad while the physical servo held home.
The coarse approach begins only after the visual track is available. Robot
computation receives no launch target, launch speed or impact/evaluation events.
Estimation updates on each usable stereo pair; precise interception can replan
about every 67 ms until 25 ms before the committed catch. Coarse approach is
planned once. These physical windows use simulation time and processing costs
use native wall time, as in the earlier records.

The optional `court-upright-presentation-release` demonstration also passed:
launch at 0.106 s, stable capture at 3.349 s, one-second retention success at
4.349 s, presentation start at 4.434 s and gesture end at 6.549 s with
`held: true`. Its dashboard preserves the upright launch, approach and full
held-ball gesture. Optional presentation does not alter the benchmark catch.

The retained cup example `upright-cup-regression` also passed one second of
retention from the same fixed home: launch at 0.101 s, stable capture at 0.617 s
and success at 1.617 s, with no floor bounces.

Current checks: **39 Python tests passed**, robot CTest (17 GoogleTests) and
scoring CTest passed. Home-policy tests cover both cup and gripper; report tests
verify historical-start labels and that presentation movement is excluded from
catch displacement. Python compilation, JavaScript syntax and whitespace checks
passed. Offline Chromium checks passed for both upright release dashboards, including
playback, scrubbing, 3D views and mobile layout without page errors. A separate
replay check verified the recorded launch joints against upright home and
inspected the held-ball endpoint of presentation.

The first upright trial, `court-upright-baseline`, missed with two bounces: the
precise-intercept planner found IK targets but no dynamically feasible approach
within the remaining time. `court-upright-visual-approach` introduced a visual
approach at 1.336 s lasting 1.3 s, but missed because joint 7 stopped about
0.65 rad short of its command. The computed-torque proportional/derivative servo
had a static-friction offset on that low-inertia joint. A bounded torque integral
was added, with gain 4 N·m/(rad·s), clamp ±1 N·m and saturation anti-windup;
actuator limits, motion constraints and ball physics are unchanged. All integral
corrections are recorded in `control.jsonl`.

The first five-throw upright run, `court-upright-validation`, scored **3/5**.
Both misses reached the coarse approach pose but received no feasible grasp
command. The planner still sampled the original home height/orientation; it now
uses the current commanded hand pose for the extra height crossing and the
low-motion orientation candidate. The early single catch
`court-upright-integral` is preserved, but does not qualify the parameter point.
The development demo `court-upright-presentation` missed before any gesture.

The temporary `court-prepared-stale-install` record came from an old installed
launch file before the first rebuild completed; its recorded initial angles show
a prepared pose. It is excluded from upright-start validation.

## Historical prepared-pose full-court practice

The `court-bounce` profile launches a 14 m/s practice lob from `[23.77, 0, 2.5]`
across the physical net, permits **two receiving-half ground bounces**, and
catches in the air near `[0.5, 0, 0.8]`. This is a deliberate change from the
original no-bounce challenge. It is a robot benchmark, not a legal tennis return
or a high-speed serve claim. All decisions, physical assumptions and adjustable
settings are in [ball-court-practice.md](ball-court-practice.md).

```zsh
source /opt/ros/humble/setup.zsh
source ros/ball_catching_ws/install/local_setup.zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --profile court-bounce --output ros/ball_catching_ws/results/my-court-run
```

The original seeds 42–46 and ±2 cm horizontal / ±1 cm vertical target variation
were retained. All five directions ran; there was no success-only filtering.

| Run | Physical catches | Actual bounces per throw | Incoming grasp speed |
| --- | --- | --- | --- |
| `court-interior-home-validation` | **5/5 — pass** | 2 | 3.093–3.116 m/s |
| `court-practice-final` (same point repeated) | **5/5 — pass** | 2 | 3.093–3.117 m/s |
| `court-practice-release` (after grasp-status latching fix) | **5/5 — pass** | 2 | 3.093–3.155 m/s |

The first two runs used the same launcher, impact coefficients, cameras, hardware limits,
finite friction and controller. Across the ten catches, launch-to-stable-capture
time was 3.232–3.244 s. The useful visual window after the final bounce was
473–524 ms. In the final repeat, visual reacquisition after the last impact took
76–124 ms; the first planned interception had 261–297 ms remaining. These are
capture-based windows, not strict last-command deadlines.

Across the ten trials, per-trial p95 perception/estimation cost was 12.78–16.17 ms;
planning 4.62–8.98 ms. In the final repeat, arm-control p95 was 0.097–0.180 ms and
gripper-control p95 0.115–0.180 ms. Physical windows use simulation time, while
these computation costs use native wall time. The two clocks must not be treated
as a certified real-time scheduling result.

The release repeat measured 3.236–3.244 s to stable capture and a 436–520 ms
post-bounce visual window. Hand-frame displacement from launch to capture was
5.8–24.9 mm across its five directions.

The recorded ball centre crossed the net region above 2.53 m, safely above the
physical net and ball-radius clearance. Ground impacts were explicitly counted
and recorded. Measured hand opening stayed within the 80 mm limit apart from
nanometre-scale numerical error; the existing DART impact/independent-actuator
limitations below remain. No force, velocity limit or ball/finger friction was
increased to obtain the final catches.

Open `results/court-practice-release/dashboard.html` for the five release replays,
impact markers, observed arrival speeds and timing margins. For bounced trials,
the headline window uses the usable track after the final bounce and its last
pre-capture visual reset. The overview preserves the whole-flight first-track
window. Court impulses are excluded from grasp-arrival speed inference.
The final repeat records a single implementation fingerprint across all five
throws, plus hashes of the six installed native artifacts in trial metadata.

The historical working profile used a side-offset 1280×960 stereo pair, visual rebound
reset, projection-based measurement covariance, prediction-confidence checks,
retry after expired interceptions, total-width stall/grasp feedback, 25 ms
motor-startup compensation, and an automatically selected interior home pose.
The original home solution sat 0.025 rad from a wrist limit; the equivalent new
30° pose provides about 0.129 rad minimum joint margin. Runtime robot inputs
remain images, calibration and hardware-observable feedback. No launch/impact
truth is passed to robot computation.

The automatic home is a **prepared catching pose near the nominal target**,
not the arm's neutral resting pose. The launch position in the final court demo
is hand-frame `[0.410, -0.0002, 0.744]` m, and displacement to stable capture
is only **20.4 mm**. The ball-centre target and hand-frame origin differ by
the tool geometry offset. These passes establish small-correction interception
and physical grasp timing with centimetre-scale target variation; they do not
establish reaching from rest or broad workspace coverage. The replay starts at
launch and includes every recorded flight phase. Dashboards now expose hand
position at launch and endpoint displacement, excluding presentation motion.

The final demonstration `court-practice-presentation-release` launched at
0.109 s, reached stable capture at 3.341 s and passed one second of retention at
4.341 s. Presentation began at 4.434 s and ended at 6.541 s with `held: true`;
both rubber-tip contacts and containment remained recorded at the end, with
ball height about 0.906 m. Its separate dashboard includes the full gesture.
The earlier `court-practice-demo` passed its catch but lost the ball during
presentation when transient grasp feedback triggered reopening. Latching a
confirmed grasp while width stays valid corrected that failure. The subsequent
`court-practice-presentation-final` held the ball through the gesture but started
movement before its longer retention interval had completed. The release
demonstration defers optional presentation until the requested hold plus 50 ms
after hardware confirmation; benchmark catches omit this gesture entirely.

Checks at this historical prepared-pose release: **37 Python tests passed**, robot CTest (17 GoogleTests) and
scoring CTest passed, with Python compilation, JavaScript syntax and whitespace
checks clean. Offline Chromium checks cover both release dashboards, playback,
scrubbing, 3D views and mobile layout without page errors.

Development failures remain available: the early single trials in
`court-bounce-first`, `court-bounce-pitched`, `court-bounce-side-camera` and
`court-bounce-visual-reset` missed; `court-bounce-higher-resolution` caught one.
The five-throw runs `court-bounce-validation` and
`court-bounce-motor-lead-validation` each scored 4/5.
`court-three-bounce-validation` scored 0/5 with the earlier planner, which could
commit to a flight before another bounce and lock out later attempts.
`court-bounce-confidence-validation` and
`court-bounce-accurate-timing-validation` each scored 0/5; the former included
physically held intervals interrupted when erroneous individual-finger stall
feedback caused the hand to reopen. Correcting total-width feedback restored
4/5 in `court-practice-validation`; selecting the interior wrist posture then
produced the first two qualifying runs above. None of these failed records were
reclassified or discarded.

## Historical prepared-pose nearby gentle baseline

Following the failed court points, the baseline now uses a nearby overhead toss.
Launch position is `[0.5, 0, 1.1]` m, nominal grasp region `[0.5, 0, 0.8]` m,
and the hand starts upward. The solver selects the upward arc, using the same
quadratic drag as the court model. The ball rises and falls freely. This changes
physical flight geometry rather than adding a controlled delay; motor limits,
friction, robot compute, and the physical retention criterion are unchanged.

```zsh
source /opt/ros/humble/setup.zsh
source ros/ball_catching_ws/install/local_setup.zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --output ros/ball_catching_ws/results/gentle-toss-validation --speeds 1.5 2
```

The original seeds 42–46 and target perturbations (±2 cm horizontally, ±1 cm
vertically) were retained. Each point ran all five directions; there was no
successful-case filtering or reduction in target variation.

| Launch speed | Catches | Measured incoming speed | Launch-to-stable-capture time | Capture-based visual window |
| --- | --- | --- | --- | --- |
| 1.5 m/s upward | **5/5 — pass** | 2.736–2.851 m/s | 0.464–0.504 s | 355–439 ms |
| 2 m/s upward | **5/5 — pass** | 2.999–3.134 m/s | 0.548–0.592 s | 418–516 ms |

The nominal unobstructed trajectories reach the 0.8 m target at about 0.444 s
and 0.524 s respectively. Physical capture adds a short arrest/settling interval;
the visual windows end at stable capture and are not strict last-command
boundaries. First planned interceptions had 244–309 ms remaining at 1.5 m/s
and 356–424 ms at 2 m/s.

Per-trial p95 perception/estimation cost was 2.07–6.74 ms; planning 3.72–18.37 ms;
arm control 0.074–0.139 ms; gripper control 0.136–0.226 ms. These are native wall
costs, distinct from the physical simulation-time windows. Recorded hand opening
stayed at or below 80 mm in all ten passing trials. This establishes a useful
repeatable simulation baseline, not a maximum speed or certified hardware result.

Some captures contact the palm before the fingertips. Arrival reporting now
uses the free-flight sample before an inferred substantial impact when that
precedes fingertip contact; it no longer labels the already-arrested ball's
near-zero speed as incoming speed. Inference uses a velocity discontinuity
relative to host gravity/drag, above the floor, between adjacent ≤12 ms records.
Those samples are labeled `before_inferred_impact`; raw records remain unchanged.
Purely vertical throws use a z target plane, avoiding division by a constant x.

The current single-trial default is 1.5 m/s upward; the default sweep is 1.5 and
2 m/s. The court challenge remains available with `--profile court`. Open
`results/gentle-toss-validation/dashboard.html` for all ten catches and their
actual arm/finger/ball motion.

A default-configuration demonstration also passed **one second of retention**
and completed optional presentation:

```zsh
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/gentle-default-demo --retention 1 --present
```

Launch was 0.101 s, stable capture 0.565 s, success 1.565 s, and presentation
recording ended at approximately 3.765 s. Its replay is in
`results/gentle-default-demo/dashboard.html`.

Earlier attempts are preserved: `gentle-feasibility-4` missed a 1.5 m horizontal
throw arriving at 3.884 m/s; `gentle-overhead-feasibility` missed a short diagonal
downward throw; five downward seeded throws in `gentle-baseline-first` scored
1/5. The 1 m/s upward point in `gentle-lofted-baseline` scored 4/5. The remaining
upward miss recorded no feasible bounded approach. Increasing launch speed from
1 to 1.5 m/s **upward** lengthens the natural flight while keeping arrival gentle;
it supplied more time for estimation and arm positioning. This shows why launch
speed alone does not order task difficulty. None of the failed attempts were
reclassified or discarded.

At this earlier stage, bounced catching had not been added. The finished court
practice section above supersedes that decision. It needs a physically appropriate
court restitution/friction model and visually estimated flight after impact.
A bounce's horizontal speed reduction depends on incidence, surface and spin;
it is not a guaranteed route from a serve to a gentle grasp. See
[Cross's measured court-impact study](https://www.physics.sydney.edu.au/~cross/PUBLICATIONS/23.%20CourtSpeed.PDF).

## Physical grasp and presentation

```zsh
source /opt/ros/humble/setup.zsh
source ros/ball_catching_ws/install/local_setup.zsh
python3 ros/ball_catching_ws/scripts/run_trial.py \
  --output ros/ball_catching_ws/results/gripper-demo-final \
  --position 0.5 0 1.4 --direction 0 0 -1 --speed 1 \
  --initial-pose 1.05279 -0.325607 -0.956798 -2.12883 2.8513 1.21818 0.56392 \
  --no-court --retention 1 --present
```

This diagnostic throw passed. It uses the stock rubber-tip collisions, friction
1.0, a 50 N total force command split between fingers, and free ball dynamics.
It is deliberately a slower overhead throw to verify physical retention
independently of the much harder full-court envelope.

- Launch: 0.105 simulated seconds; stable capture: 0.441 s; success: 1.441 s.
- One full second of continuous bilateral contact, between-finger location,
  low relative ball/hand speed, and no prior floor contact.
- Measured incoming speed: 3.453 m/s before the first finger contact.
- First usable visual track: 72 ms after launch; capture-based reaction window:
  264 ms. First planned interception had 167 ms remaining.
- Presentation began at 0.734 s and remained physically held through the end
  of recording at approximately 3.641 s. The ball rose about 10 cm, ending at
  0.896 m with both rubber tips in contact and opening approximately 67.04 mm.
- Per-update p95 costs: perception/estimation 5.866 ms, planning 5.673 ms,
  arm control 0.127 ms, gripper control 0.157 ms. Observation age p95: 13 ms.

Open `results/gripper-demo-final/dashboard.html` in a browser to inspect the
catch and presentation. This is one development trial, not a qualifying 5/5
court data point. Earlier development runs exposed an evaluator bug: Fortress
stores contact data on collision entities, not sensor entities. Correcting that
lookup enabled independent physical scoring; earlier failed records remain
unchanged.

## Full-court exploration

```zsh
python3 ros/ball_catching_ws/scripts/run_experiment.py \
  --output ros/ball_catching_ws/results/court-exploration \
  --profile court --speeds 3 20 40 67.056
```

Opposite-baseline launcher `[23.77, 0, 2.5]` m; nominal target `[0.5, 0, 0.8]` m;
physical net and no bounce; drag coefficient 0.55. The same seeds 42–46 perturb
catch targets by ±2 cm horizontally and ±1 cm vertically at each point.
Speeds specify launch magnitude. Five native trials ran at each valid point,
with presentation disabled and retention 0.2 s. No artificial delays were added.

| Launch speed | Catches | Result | Recorded incoming speed | Capture-based visual reaction window |
| --- | --- | --- | --- | --- |
| 3 m/s | No trials | Invalid geometry: no net/floor-clearing arc | Unavailable | Unavailable |
| 20 m/s | 0/5 | Fail | 13.82–13.83 m/s where available | 274–358 ms where available |
| 40 m/s | 0/5 | Fail | 25.12–25.17 m/s | 69–146 ms |
| 67.056 m/s (150 mph) | 0/5 | Fail | 41.60–41.68 m/s | −101 ms to 40 ms where available |

All 15 valid throws ended with floor contact before satisfying physical retention.
At 20 m/s, two throws produced interception plans but did not retain the ball;
the other throws produced no feasible bounded approach. No feasible interception
plans were recorded at 40 or 67.056 m/s. One fastest throw never produced a usable
visual state; another produced its first state after the nominal target-plane
crossing. Missing arrival/window measurements remain unavailable, rather than
substituting predicted values or claiming a zero margin.

The detector's minimum apparent ball size and six-observation filter bootstrap
mean the robot often acquires a track only near the receiving end of the court.
Total launch-to-floor durations were 1.77–2.01 s at 20 m/s, 1.05–1.22 s at 40 m/s,
and 0.67–0.88 s at 67.056 m/s. Those durations include travel after missed
interception, so they are not available reaction budgets.

Across court trials, per-trial perception p95 was 2.39–6.30 ms; planning p95 was
0.75–10.17 ms where planning ran; arm-control p95 was 0.078–0.145 ms; gripper-control
p95 was 0.094–0.204 ms. These are native wall-clock costs. Physical windows use
simulation time and start at the capture timestamp of the first usable track;
observation age is reported separately. They establish neither a hard real-time
deadline nor a maximum achievable speed. Current planner choices, initial pose,
visibility, estimation and physical impact all affect these failed points.
No claim is made that the robot's inherent limits have been found.

Open `results/court-exploration/dashboard.html` for the parameter table, all 15
throw replays and individual timings. The report separates invalid geometry,
completed failed points and infrastructure failures. Only five completed catches
qualify a passing point; none of these court points qualify.

## Hardware-model limits

The motor servo requests at most 50 mm/s per finger, the URDF opening is 80 mm,
and forces are bounded. Gazebo/DART joint velocity bounds are configured.
However, recorded collision impulses briefly exceeded those travel bounds:
the diagnostic grasp reached approximately 0.163 m/s in a 4 ms position interval;
a 40 m/s court miss briefly reached approximately 1.97 m/s and an 86.45 mm opening.
These are impact-driven simulator overshoots, not commanded motor speeds.
Independent simulated actuators also approximate the commercial hand's coupling.
The retained development ball did not slip away, but this model is not a
validated commercial hand impact/compliance model. High-speed failures must
not all be attributed to computation or friction.

## Earlier regression and checks

The retained cup example passed a one-second retention regression:

```zsh
python3 ros/ball_catching_ws/scripts/run_trial.py --mode cup \
  --output ros/ball_catching_ws/results/cup-regression-gripper --no-dashboard
```

Launch was 0.101 s, stable cup containment 0.617 s, and success 1.617 s.

- Native Release builds completed with testing enabled.
- `colcon test` / `test-result`: **17 tests, zero failures or errors**.
- Workspace pytest: **29 passed**; one upstream xacro deprecation warning.
- Flight tests cover net/floor rejection, quadratic drag, speed semantics and
  invalid court geometry, vertical/lofted throws, and pre-impact arrival reporting.
  C++ tests cover drag identification from observations,
  nonzero interception velocity/braking continuity and physical score rejection.
- JavaScript syntax checks, Python compilation and `git diff --check` passed.
- Installed Chromium/Playwright checked the offline dashboard: loading without
  network assets, WebGL replay, playback, scrubbing, view switching, and mobile
  layout without horizontal overflow or page errors. Desktop/mobile screenshots
  were visually inspected.

No gem5/Chimaera guest run, optimizer, injected-delay study or calibrated
material/actuator model was validated in this change.
