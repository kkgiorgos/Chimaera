# Native FR3 airborne ball grasping

Status: agreed scope implemented; native validation and measured limitations
are recorded in [ball-grasping-validation.md](ball-grasping-validation.md).
This extends the [cup-catching application](ball-catching-design.md).

Every mode and profile starts from the same fixed upright home joint angles:
`[0°, −45°, 0°, −135°, 0°, 90°, 45°]`. Custom initial poses are rejected.
The robot may approach its workspace centre after a visual incoming-ball track,
then refine interception from the visual flight estimate. All movement occurs
during flight and is included in replay and timing. Historical prepared-pose
passes are preserved separately and do not qualify the upright-start version.

## Accepted scope

- Use the stock Franka Hand finger geometry, including the existing rubber-tip
  geometry, and realistic hardware limits. Do not add pads or custom fingers.
- Keep a simple rigid tennis-sized ball. Material behavior should support
  robust retention so incidental slipping does not obscure perception and
  computation limits. Validate finite friction and physical contact rather
  than attaching the ball artificially.
- Allow wrist orientation changes and catching motion appropriate to the
  incoming flight. Preserve arm motion and torque limits.
- Use visual observations and stock hardware feedback only. Ball ground truth
  and experiment launch information remain exclusive to host evaluation and
  experiment generation. Do not expose simulated tactile maps or fingertip
  forces as robot feedback.
- Require airborne capture and physical retention between the fingers. The
  gentle/no-bounce challenge prohibits prior floor contact; court practice
  explicitly permits a configured number of receiving-half ground bounces. Palm contact is permitted. Use a configurable retention interval
  defaulting to 0.2 simulated seconds, validated against longer development holds.
- Provide an optional movement that presents the caught ball. Benchmark runs
  omit that movement and end after the retention criterion. Demonstration
  presentation must not change the catch score or its recorded time.
- Keep the cup available where practical; compatibility may change when it
  materially reduces implementation complexity.
- Keep native ROS 2 Humble/Gazebo Fortress, C++ robot computation, Python
  orchestration, and the existing future Chimaera host/guest separation.

## Hardware and contact findings

The existing local `franka_hand.xacro` models rubber gripping tips with box
collisions. Preserve their geometry unless a subsequent requirement explicitly
allows additional pads. Configure their material contact properties explicitly;
the selected finite friction is 1.0 on the ball and fingers. Native development
throws validate bilateral retention, including an optional presentation movement.

The Franka Hand product manual specifies an 80 mm travel range, 50 mm/s travel
speed per finger, and adjustable continuous grasping force of 30–70 N. These
take precedence over the local URDF's 200 mm/s finger velocity and 100 N joint
effort bounds. The simulation conservatively splits a 50 N total force command
into 25 N per finger, with host caps of 35 N each. This convention is an approximation rather
than a calibrated model of the commercial motor/servo.

The pinned libfranka `GripperState` exposes width, maximum width, grasp status,
temperature, and timestamp. It does not expose a tactile map or measured
fingertip force. Detailed simulator contacts must not become robot feedback
unless an explicit sensor model corresponding to additional hardware is agreed.
Grasp-status feedback must reproduce a hardware-observable criterion, rather
than checking a simulator entity's identity or true ball position.

Validate holding and deceleration with the selected physics engine before
attributing failed airborne trials to computation. Record contact parameters
and distinguish slipping, impact/bounce, alignment, and late actuation where
the evidence supports that diagnosis. Finite friction and hardware limits still
constrain how much incoming momentum can be arrested.

## Experiment requirements

- Include air resistance in physical flight and robot prediction. Begin with
  gravity and quadratic drag, without wind, spin lift, or a deformable ball.
  The robot estimates drag from successive visual observations; it receives no
  physical drag coefficient from the experiment.
- Establish a nearby gentle-toss baseline before studying high-speed challenges.
  The default launches upward from `[0.5, 0, 1.1]` m at 1.5 m/s toward a nominal
  `[0.5, 0, 0.8]` m grasp region, with the hand initially upward. Test five seeded
  directions at each point; preserve the same ±2 cm horizontal and ±1 cm vertical
  target perturbations. The physical loft extends observation time without delay
  injection. The stock motor limits and contact model remain unchanged.
- Retain a separate tennis-court challenge: opposite baselines, a 23.77 m court,
  physical net and 2.5 m launcher. Keep the original no-bounce challenge, and
  provide a bounced practice profile with an explicit passive court-impact law,
  recorded bounce counts, and visually reacquired post-bounce flight. See
  [court decisions and adjustable settings](ball-court-practice.md).
- Evaluate explicit parameter data points with five seeded throws per point.
  A point passes only with 5/5 catches. This is an observed experiment criterion,
  not a claim of a guaranteed catch probability.
- Automate reproducible variation in launch direction around a nominal target.
- Verify that each generated ballistic path enters an appropriate robot
  catching region. Geometry validity must be independent of the tested
  perception/planning pipeline; dynamically difficult throws remain valid
  benchmark cases rather than being silently rejected.
- Control launch distance as well as speed. Record both and flight time so
  distance changes do not obscure the measured speed limit.
- Keep launch position fixed within a speed comparison, with position/distance
  configurable across comparisons. Record launch and actual arrival speeds.
  Specified speeds mean launch speed; actual arrival speed is measured separately.
- A proposed upper exploration setting is 150 mph, approximately 67.06 m/s;
  it is not a tennis record claim or an expected robot capability.
- Do not implement binary search or an optimizer in this version. A future
  optimizer may search within a supplied time budget. State the tested geometry,
  trial distribution, and observed success fraction for the explicit points.
- Reuse seeded throw cases across compared configurations. Identical repeats
  test reproducibility; direction variation tests robustness and must be
  reported separately when possible.
- Record computation durations for perception/estimation, planning, arm control,
  and gripper control, along with flight duration and actual joint/ball motion.
  Present a small overview of outcomes, timing, and the available reaction window.
  Flight duration and first-usable-visual-track-to-interception time are distinct:
  a distant ball may not be observable throughout its flight.
- Distinguish simulated-time physical margins from native wall-clock processing
  costs. A simulator running slowly does not establish a real-time deadline.
- Do not add artificial delays or delay-injection experiments.
- Provide a user-friendly dashboard comparing data points and individual throws,
  with synchronized robot/ball replay, scrubbing, and slow playback. Replay is
  offline evaluation and must not feed privileged state back into robot compute.

## Dashboard and presentation

Generate a self-contained local HTML dashboard with timing, outcomes and offline
3D replay of actual ball/robot/finger motion. No hosted service or network assets
are required. Presentation tries a raised, camera-facing pose, reducing the
rotation when joint limits make the full turn infeasible. It is optional and
excluded from benchmark duration. Catch time remains independent of presentation.

The nearby gentle profile passes five seeded throws at both 1.5 and 2 m/s
launch speeds. The native one-second hold/presentation demonstration verifies
physical retention; full-court five-throw points separately evaluate the current
perception/planning envelope. The two-bounce full-court practice profile passed
two consecutive five-throw runs with the same original direction variation.
A failed point is retained rather than tuning the
throw distribution until it succeeds.

## Sources

- [Franka Hand product manual, mechanical data](https://download.franka.de/documents/220010_Product%20Manual_Franka%20Hand_1.2_EN.pdf)
- [libfranka gripper feedback](https://frankarobotics.github.io/libfranka/0.15.0/structfranka_1_1GripperState.html)
- Local `ros/franka_ws/src/franka_description/end_effectors/common/franka_hand.xacro`
- Local `ros/franka_ws/src/libfranka/include/franka/gripper_state.h`
- [ITF tennis rules and court dimensions](https://www.itftennis.com/en/about-us/governance/rules-and-regulations/)
- [Cross and Lindsey: measured tennis-ball aerodynamics](https://twu.tennis-warehouse.com/learning_center/aerodynamics2.php)
