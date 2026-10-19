# Ball-catching estimator diagnosis, 2026-10-18

Follow-up: the corrected filter still missed a later coupled baseline because
long planner callbacks masked useful observations. The native/instantaneous
checks below did not establish full-system reliability. See
[the successful pop-os comparison and planner timing diagnosis](ball-catching-planner-timing.md).

The missed 3 m/s loft is achievable. The live experiment
`ce4e4b3c98c94915ba4410ec29d095b4` exposed an existing visual-estimation defect:
stereo uncertainty was calculated but never used by the ball filter, and the
filter's initial covariance was unrelated to its initial visual fit.

## Evidence

The inspected snapshot contained eight scored misses: five `loft-3` cases and
three `loft-5` cases, including their unloaded baselines. Every scored case had
zero feasible plans. The 3 m/s baseline made eight searches, with valid visual
tracking and no expired perception records. Offline replay reproduced all eight
rejections from its recorded visual states, arm feedback and inferred radius.
Where IK succeeded, the approach violated joint acceleration limits, primarily
joint 4. This was not a scoring-only failure.

The current and previously successful LPDDR5 baselines have the same four
3 GHz TimingSimpleCPU cores, Ruby MESI two-level hierarchy, private 32 KiB/8-way
instruction and data L1s, shared 8 MiB/16-way L2, 3 GiB guest RAM and identical
LPDDR5-6400 configuration hash. The 480×360, 30 Hz camera, planner/control rates,
0.5 ms coupling, cup geometry and 3 m/s throw also match. The material startup
change is restoring a readiness checkpoint and replaying the host instead of
booting with KVM and switching to detailed CPUs.

All five inspected current 3 m/s cases have identical first nine triangulated
observations. Their first detection is 29.5 ms after launch. The successful
historical baseline has a different camera/launch phase. The checkpoint startup
therefore repeats a visual sampling pattern that the old filter handles poorly;
these data do not demonstrate corrupted serialized state.

For example, at the failed baseline's search 342.5 ms after launch, the old
filter estimates horizontal velocity at approximately −1.619 m/s, compared with
the verified launch velocity −1.342 m/s. Its prediction at a candidate catch
height is around x=0.34 m. Correct uncertainty handling estimates −1.300 m/s and
produces a feasible candidate around x=0.54 m. Ball ground truth is used only for
diagnosis and test assertions, never as a controller input.

## Correction

`Perception` now passes `Stereo::observationNoise()` to `BallFilter::observe()`.
The five-observation ballistic initialization uses weighted least squares and
derives the full position/velocity covariance, including cross-correlation,
from those observations. Subsequent Kalman updates use each observation's
stereo covariance. Invalid covariance matrices are rejected before state changes.

The process-noise constants, search heights, IK, joint speed/acceleration limits,
cup tilt/braking constraints, physical setup and scoring criteria are unchanged.
The default constant-noise filter reference was regenerated with independent
NumPy least squares and covariance calculations.

## Validation and scope

The known successful configuration was used: position `[1.8, 0.04, 3]`, direction
`[-1, 0, 2]`, launch speed 3 m/s; camera `[-2, 0, 2]`, 480×360 at 30 Hz;
planner 30 Hz with 60 ms plan interval; effort control 250 Hz; joint feedback
1000 Hz; 0.5 ms physics; 0.12 m cup radius, 0.14 m depth; 1.5 s scoring window.

- All 25 C++ checks passed: 23 core tests and two runtime tests, both in the
  isolated Ubuntu 22.04/ROS Humble container and in the local ROS Jazzy build.
  New coverage includes the actual failed baseline observations, a feasible
  catch trajectory under the existing limits, closed-form initial covariance,
  and invalid covariance inputs.
- Corrected C++ offline replay finds a feasible first plan in all five recorded
  current 3 m/s misses, at 307.5–342.5 ms after launch. The old recorded states
  produce no feasible plans. In the previously successful unloaded baseline,
  the corrected replay finds a plan at 185.5 ms, compared with the original
  383.5 ms. This is candidate feasibility, not a reconstructed catch score.
- Three isolated native Gazebo Fortress repetitions of the complete correction
  scored **3/3 catches**, with 0.5315, 0.5320 and 0.5315 s final continuous
  containment. All three ballistic launch checks passed. These repetitions
  establish native viability, not a statistical reliability guarantee or a
  full-system gem5 validation.
- The intermediate update-only covariance correction scored a native miss with
  no feasible plan. Correct initial covariance is also necessary; that partial
  correction was not retained.

The recorded 5 m/s cases have a separate acquisition problem: first detection
occurs at 425.5 ms and first track at 557.5 ms after launch. An independent
feasibility check using the true ballistic state still finds candidates at
550 ms, but none at 600 ms from the stationary home pose. Visual acquisition
and subsequent planning leave very little margin. The estimator correction
alone does not recover those recorded cases. The 5/7 m/s configurations need
separate qualification; they are not treated as known successful references.

No new gem5 run was started. The live experiment, its collected results, images
and readiness checkpoints were not changed. Future use of this correction
requires a new application build, guest bundle and compatible readiness
checkpoint; do not hot-patch a frozen measurement cohort.

## Local records and reproduction

Records are under
`results/ball-catching-estimator-debug-20261018/` (ignored experiment artifacts):

- `validation-summary.json`: native outcomes, launch checks, source/binary hashes.
- `validation-final.log`: Humble build, CTest and three native results.
- `validate-native-final.sh`: exact build/test/native trial commands. It ran in
  a separate `--network none` container using the existing application image,
  with a read-only repository and a separate writable build/install overlay.
- `native-weighted-{1,2,3}/`: complete native motion, perception, plans and scores.
- `offline/`: original experiment snapshot, downloaded raw recordings, C++
  filter/intercept replay harnesses, replay driver/results and the independent
  reference-update script. The replay driver stops previous successful
  recordings at their original first plan to avoid interpreting their later
  arm motion as a corrected execution. Six of eight previous catch recordings
  retain a feasible plan in that restricted window; the other two require a
  new execution/schedule and are not claimed as recovered catches.

The raw archive can be extracted into a temporary directory. Build
`filter_replay.cpp` against the corrected `catching_core` library and
`intercept_replay.cpp` against that library plus its URDF/KDL dependencies, then
run `offline/replay.py --filter-binary FILTER --planner-binary PLANNER --root RECORDINGS`.
The planner harness deliberately evaluates the existing candidate search without
simulating execution or applying a new planning schedule.
