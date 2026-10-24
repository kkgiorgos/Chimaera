#include "ball_catching_robot/core.hpp"

#include <gtest/gtest.h>
#include <json-c/json.h>
#include <unistd.h>
#include <urdf/model.h>

#include <Eigen/Eigenvalues>
#include <fstream>
#include <functional>
#include <map>
#include <opencv2/imgproc.hpp>

#include "ball_catching_robot/image_freshness.hpp"

using namespace ball_catching;
TEST(ImageFreshness, ExactBudgetFutureAndUnavailableTime)
{
  constexpr int64_t budget = 22222223;
  EXPECT_TRUE(fresh_image(100000000, 100000000 + budget, budget));
  EXPECT_FALSE(fresh_image(100000000, 100000001 + budget, budget));
  EXPECT_FALSE(fresh_image(100000000, 99999999, budget));
  EXPECT_FALSE(fresh_image(0, std::nullopt, budget));
  EXPECT_FALSE(fresh_image(-1, 100, budget));
}
TEST(ImageFreshness, NativeUsesSimulationClock)
{
  int64_t simulation = 300000000;
  ImageClock clock([&] { return simulation; });
  EXPECT_EQ(clock.now(), simulation);
  simulation += 100000000;
  EXPECT_EQ(clock.now(), simulation);
  EXPECT_THROW(ImageClock([] { return 0; }, "/missing-clock", ""), std::invalid_argument);
  EXPECT_THROW(
    ImageClock([] { return 0; }, "/missing-clock", "/missing-origin"), std::runtime_error);
}
TEST(ImageFreshness, AdmissionReservesMeasuredProcessingTime)
{
  ImageAdmission admission(66666667);
  EXPECT_TRUE(admission.accepts(100000000, 149000000));
  admission.observe(100000000, 138000000);
  EXPECT_TRUE(fresh_image(100000000, 149000000, 66666667));
  EXPECT_FALSE(admission.accepts(100000000, 149000000));
  EXPECT_TRUE(admission.accepts(133000000, 149000000));
  EXPECT_FALSE(admission.accepts(150000000, 149000000));
  EXPECT_FALSE(admission.accepts(133000000, std::nullopt));
}
TEST(ImageFreshness, AdmissionRecoversFromOutliersAndRejectsInvalidMeasurements)
{
  ImageAdmission admission(100);
  admission.observe(100, 1100);
  EXPECT_EQ(admission.estimate(), 90);
  EXPECT_TRUE(admission.accepts(100, 100));
  admission.observe(std::nullopt, 1100);
  admission.observe(1100, 100);
  EXPECT_EQ(admission.estimate(), 90);
  for (int i = 0; i < 30; ++i) admission.observe(100, 120);
  EXPECT_EQ(admission.estimate(), 20);
  EXPECT_TRUE(admission.accepts(100, 180));
  EXPECT_FALSE(admission.accepts(100, 181));
  EXPECT_THROW(ImageAdmission(0), std::invalid_argument);
}
TEST(ImageFreshness, SkippedFreshPairsCannotPermanentlyBlockAdmissionAfterAnOutlier)
{
  ImageAdmission admission(100);
  admission.observe(100, 1100);
  EXPECT_FALSE(admission.accepts(100, 201));
  EXPECT_FALSE(admission.accepts(100, std::nullopt));
  EXPECT_EQ(admission.estimate(), 90);  // Invalid/stale traffic is not new evidence.
  EXPECT_FALSE(admission.accepts(100, 120));
  EXPECT_LT(admission.estimate(), 90);
  bool accepted = false;
  for (int i = 0; i < 10 && !accepted; ++i)
    accepted = admission.accepts(100 + i * 100, 120 + i * 100);
  EXPECT_TRUE(accepted);  // Recover even though no processing could be observed.
}
TEST(ImageFreshness, GuestClockAdvancesDespiteDelayedRosClock)
{
  const std::string path = "/tmp/chimaera-clock-test-" + std::to_string(getpid());
  std::remove(path.c_str());
  void * library = dlopen(FAKE_GEM5_CLOCK, RTLD_NOW | RTLD_LOCAL);
  ASSERT_NE(library, nullptr);
  auto set = reinterpret_cast<void (*)(uint64_t)>(dlsym(library, "chimaera_test_set_time_ns"));
  ASSERT_NE(set, nullptr);
  ImageClock clock([] { return 0; }, FAKE_GEM5_CLOCK, path);
  EXPECT_FALSE(clock.now());  // Fail closed before the exact origin is available.
  const uint64_t origin = 3000000000;
  std::ofstream(path) << origin << '\n';
  set(origin + 90000000);
  EXPECT_EQ(clock.now(), 90000000);
  EXPECT_TRUE(fresh_image(80000000, clock.now(), 22222223));
  // Processing can use up the budget while /clock remains unchanged.
  set(origin + 130000000);
  EXPECT_FALSE(fresh_image(80000000, clock.now(), 22222223));
  set(origin - 1);
  EXPECT_FALSE(clock.now());
  std::remove(path.c_str());
  dlclose(library);
}
namespace
{
std::string robot()
{
  std::ifstream file(TEST_URDF);
  return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
json_object * member(json_object * object, const char * key)
{
  json_object * result = nullptr;
  if (!json_object_object_get_ex(object, key, &result))
    throw std::runtime_error("Missing reference field");
  return result;
}
template <int N>
Eigen::Matrix<double, N, 1> vector(json_object * array)
{
  Eigen::Matrix<double, N, 1> result;
  for (int i = 0; i < N; ++i)
    result[i] = json_object_get_double(json_object_array_get_idx(array, i));
  return result;
}
struct Reference
{
  json_object * data = json_object_from_file(PYTHON_REFERENCE);
  Reference()
  {
    if (!data) throw std::runtime_error("Cannot load Python reference");
  }
  ~Reference() { json_object_put(data); }
};
Vec7 neutral()
{
  return (Vec7() << 0., -std::acos(-1.) / 4, 0., -3 * std::acos(-1.) / 4, 0., std::acos(-1.) / 2,
          std::acos(-1.) / 4)
    .finished();
}
}  // namespace

TEST(Stereo, MetricScaleWithoutKnownBallSize)
{
  Stereo stereo(400, 320, 240, .3, Vec3(-.65, .15, 1));
  const auto point = stereo.point({340, 220, 8}, {280, 220, 8});
  ASSERT_TRUE(point);
  EXPECT_LT((*point - Vec3(1.35, .05, 1.1)).norm(), 1e-12);
  EXPECT_FALSE(stereo.point({340, 220, 8}, {340, 220, 8}));
  EXPECT_FALSE(stereo.point({340, 220, 8}, {280, 230, 8}));
}
TEST(Stereo, ColorDetectionAndInferredRadius)
{
  Stereo stereo(400, 320, 240, .3, Vec3(-.65, .15, 1));
  cv::Mat left = cv::Mat::zeros(480, 640, CV_8UC3), right = left.clone();
  cv::circle(left, {340, 220}, 8, cv::Scalar(190, 255, 3), -1);
  cv::circle(right, {280, 220}, 8, cv::Scalar(190, 255, 3), -1);
  const auto detection = stereo.detect(left, right);
  ASSERT_TRUE(detection);
  EXPECT_LT((detection->point - Vec3(1.35, .05, 1.1)).norm(), 1e-6);
  EXPECT_NEAR(detection->radius, .04, .001);
  EXPECT_FALSE(stereo.detect(cv::Mat::zeros(480, 640, CV_8UC3), right));
}
TEST(Stereo, InvalidCalibration)
{
  EXPECT_THROW(Stereo(0, 320, 240, .3, Vec3::Zero()), std::invalid_argument);
  EXPECT_THROW(Stereo(400, 320, 240, -.3, Vec3::Zero()), std::invalid_argument);
}
TEST(Filter, ExactBallisticVelocityAndPrediction)
{
  BallFilter filter;
  const Vec3 p(2, -.2, 1.6), v(-4, .5, 1);
  std::optional<Vec6> state;
  for (int i = 0; i < 19; ++i) {
    const double t = i * .2 / 18;
    state = filter.observe(p + v * t + .5 * gravity * t * t, t);
  }
  ASSERT_TRUE(state);
  EXPECT_LT((state->head<3>() - (p + v * .2 + .5 * gravity * .2 * .2)).norm(), 1e-10);
  EXPECT_LT((state->tail<3>() - (v + gravity * .2)).norm(), 1e-10);
  EXPECT_LT((*filter.predict(.35) - (p + v * .35 + .5 * gravity * .35 * .35)).norm(), 1e-10);
  EXPECT_FALSE(filter.observe(p, .2));
}
TEST(Filter, StereoQuantizationPreservesAchievableLoftIntercept)
{
  // First nine detections from the missed, unloaded loft-3 checkpoint trial
  // ce4e4b3c98c94915ba4410ec29d095b4 / case-000. Only visual observations
  // enter the filter; the launch velocity is used below as a test oracle.
  const std::vector<Vec3> observations{
    {1.7480091379843832, .04375, 3.08125},
    {1.6715191555765387, .04591836734693877, 3.144897959183673},
    {1.6715191555765387, .04591836734693877, 3.22219387755102},
    {1.639686137531708, .04682078194699553, 3.275867396236899},
    {1.5980887724650081, .0465, 3.32},
    {1.5801878332985155, .04701492537313433, 3.361194029850746},
    {1.4597007427548152, .046153846153846156, 3.35},
    {1.4597007427548152, .046153846153846156, 3.378846153846154},
    {1.3944233702500077, .048113207547169815, 3.3669811320754715}};
  Stereo stereo(480. / (2 * std::tan(1.35 / 2)), 240., 180., .3, Vec3(-2., .15, 2.));
  BallFilter filter;
  std::optional<Vec6> state;
  for (size_t i = 0; i < observations.size(); ++i)
    state =
      filter.observe(observations[i], .4955 + .033 * i, stereo.observationNoise(observations[i]));
  ASSERT_TRUE(state);
  EXPECT_NEAR((*state)[3], -3. / std::sqrt(5.), .15);

  // The same search time, inferred radius and feedback as the failed trial.
  // Keep the existing joint limits, cup tilt and braking constraints intact.
  Arm arm(robot());
  const auto home = uprightHome();
  const double start = .8085, age = start - .7595, radius = .030553017972351902;
  const Vec3 position = state->head<3>() + state->tail<3>() * age + .5 * gravity * age * age;
  const Vec3 velocity = state->tail<3>() + gravity * age;
  const double height = arm.pose(home).p.z() - .15;
  const double flight =
    (velocity.z() +
     std::sqrt(velocity.z() * velocity.z() + 2 * 9.81 * (position.z() - height - radius - .005))) /
    9.81;
  Vec3 point = position + velocity * flight + .5 * gravity * flight * flight;
  point.z() = height;
  Vec7 feedback;
  feedback << 0., .005741797915, 0., -.1269895667, 0., .4806253381, .7853981634;
  auto target = arm.inverse(point, arm.pose(neutral()).M, feedback, 120);
  if (!target) target = arm.inverse(point, arm.pose(neutral()).M, neutral(), 120);
  ASSERT_TRUE(target);
  const auto motion = cupCatchTrajectory(
    arm, start, flight, {home, Vec7::Zero(), Vec7::Zero()}, *target, velocity + gravity * flight,
    1.5, .4);
  ASSERT_TRUE(motion);
  for (const auto & segment : *motion) EXPECT_TRUE(segment.feasible(arm));
}
TEST(Filter, InvalidObservationCovarianceDoesNotAdvanceTheFilter)
{
  BallFilter filter;
  Eigen::Matrix3d noise = Eigen::Matrix3d::Identity();
  noise(0, 0) = -1.;
  EXPECT_FALSE(filter.observe(Vec3(1., 0., 2.), .1, noise));
  noise.setZero();
  EXPECT_FALSE(filter.observe(Vec3(1., 0., 2.), .1, noise));
  noise.setIdentity();
  noise(0, 1) = .5;
  EXPECT_FALSE(filter.observe(Vec3(1., 0., 2.), .1, noise));
  noise.setIdentity();
  noise(0, 0) = NAN;
  EXPECT_FALSE(filter.observe(Vec3(1., 0., 2.), .1, noise));
  std::optional<Vec6> state;
  for (int i = 0; i < 5; ++i)
    state = filter.observe(Vec3(1., 0., 2.) + .5 * gravity * i * i * .0001, i * .01);
  EXPECT_TRUE(state);
}
TEST(Filter, InitialCovarianceComesFromTheVisualFit)
{
  BallFilter filter;
  Eigen::Matrix3d noise;
  noise << .0004, -.00001, .0001, -.00001, .00002, -.000002, .0001, -.000002, .00005;
  const Vec3 p(1.8, .04, 3.), v(-3. / std::sqrt(5.), 0., 6. / std::sqrt(5.));
  constexpr double step = .033;
  std::optional<Vec6> state;
  for (int i = 0; i < 5; ++i) {
    const double t = i * step;
    state = filter.observe(p + v * t + .5 * gravity * t * t, t, noise);
  }
  ASSERT_TRUE(state);
  EXPECT_LT((state->tail<3>() - v - gravity * (4 * step)).norm(), 1e-9);
  // Closed-form covariance for five equally spaced observations, expressed at
  // the final observation: Var(p)=0.6 R, Var(v)=R/sum((t-mean(t))^2).
  const auto & covariance = filter.covariance();
  EXPECT_LT((covariance.topLeftCorner<3, 3>() - .6 * noise).norm(), 1e-12);
  EXPECT_LT((covariance.bottomRightCorner<3, 3>() - noise / (10 * step * step)).norm(), 1e-12);
  EXPECT_LT((covariance.topRightCorner<3, 3>() - noise / (5 * step)).norm(), 1e-12);
}
TEST(Motion, NonzeroTerminalVelocityAndContinuousBraking)
{
  const MotionState start{Vec7::Zero(), Vec7::Zero(), Vec7::Zero()};
  const MotionState atCatch{Vec7::Ones(), Vec7::Constant(.2), Vec7::Zero()};
  Quintic approach(0., 1., start, atCatch);
  const auto end = approach.sample(1.);
  EXPECT_LT((end.q - atCatch.q).norm(), 1e-10);
  EXPECT_LT((end.dq - atCatch.dq).norm(), 1e-10);
  Quintic brake(1., .4, atCatch, (atCatch.q + atCatch.dq * .2).eval());
  EXPECT_LT((brake.sample(1.).dq - end.dq).norm(), 1e-10);
  EXPECT_LT(brake.sample(1.4).dq.norm(), 1e-10);
}
TEST(Planning, BoundsFailedWorkAndRecoversObservationHiddenByLongSearch)
{
  auto * reference = json_object_from_file(PLANNER_REFERENCE);
  ASSERT_NE(reference, nullptr);
  Arm arm(robot());
  CupInterceptPlanner planner(arm);
  auto * rows = member(reference, "rows");
  for (size_t i = 0; i < json_object_array_length(rows); ++i) {
    auto * row = json_object_array_get_idx(rows, i);
    const double time = json_object_get_double(member(row, "time"));
    const double captured = json_object_get_double(member(row, "capture_time"));
    const double start = time + 1. / 30.;
    const double age = start - captured;
    const auto state = vector<6>(member(row, "state"));
    const Vec3 position = state.head<3>() + state.tail<3>() * age + .5 * gravity * age * age;
    const Vec3 velocity = state.tail<3>() + gravity * age;
    EXPECT_LT(json_object_get_double(member(row, "completed_time")), time);
    const auto plan = planner.search(
      start, {uprightHome(), Vec7::Zero(), Vec7::Zero()}, vector<7>(member(row, "feedback")),
      position, velocity, json_object_get_double(member(row, "radius")));
    EXPECT_EQ(!plan.segments.empty(), json_object_get_boolean(member(row, "feasible")) != 0);
    EXPECT_LE(plan.inverseCalls, 18);
    EXPECT_LE(plan.inverseIterations, plan.inverseCalls * 35);
    if (i == 0) EXPECT_EQ(plan.inverseIterations, 490);  // Previously 1,680.
    if (i == 0) {
      const auto cancelled = planner.search(
        start, {uprightHome(), Vec7::Zero(), Vec7::Zero()}, vector<7>(member(row, "feedback")),
        position, velocity, json_object_get_double(member(row, "radius")), [] { return true; });
      EXPECT_TRUE(cancelled.interrupted);
      EXPECT_TRUE(cancelled.segments.empty());
      EXPECT_EQ(cancelled.inverseIterations, 0);
    }
    if (i == 1) {
      EXPECT_LT(
        time, json_object_get_double(member(reference, "recorded_first_search_completed_time")));
      ASSERT_FALSE(plan.segments.empty());
      EXPECT_DOUBLE_EQ(plan.segments.front().start, start);
      for (const auto & segment : plan.segments) EXPECT_TRUE(segment.feasible(arm));
      int polls = 0;
      const auto partial = planner.search(
        start, {uprightHome(), Vec7::Zero(), Vec7::Zero()}, vector<7>(member(row, "feedback")),
        position, velocity, json_object_get_double(member(row, "radius")),
        [&] { return ++polls >= 10; });
      EXPECT_TRUE(partial.interrupted);
      ASSERT_FALSE(partial.segments.empty());
      EXPECT_LT(partial.inverseIterations, plan.inverseIterations);
      for (const auto & segment : partial.segments) EXPECT_TRUE(segment.feasible(arm));
    }
  }
  json_object_put(reference);
}
TEST(Motion, FutureReplacementPreservesActiveMotionUntilItsTimestamp)
{
  const MotionState home{Vec7::Zero(), Vec7::Zero(), Vec7::Zero()};
  TrajectorySchedule schedule;
  EXPECT_TRUE(schedule.empty());
  Quintic first(1., 1., home, Vec7::Ones());
  schedule.replace({first}, .9);
  EXPECT_LT(schedule.sample(.99, home).q.norm(), 1e-12);
  const auto join = first.sample(1.5);
  Quintic second(1.5, 1., join, Vec7::Constant(2.));
  schedule.replace({second}, 1.2);
  EXPECT_LT((schedule.sample(1.3, home).q - first.sample(1.3).q).norm(), 1e-12);
  EXPECT_LT((schedule.sample(1.5, home).dq - join.dq).norm(), 1e-12);
  EXPECT_LT((schedule.sample(1.7, home).q - second.sample(1.7).q).norm(), 1e-12);
  // A further future update must not erase the preceding scheduled interval.
  Quintic third(1.8, 1., second.sample(1.8), Vec7::Constant(3.));
  schedule.replace({third}, 1.3);
  EXPECT_LT((schedule.sample(1.7, home).q - second.sample(1.7).q).norm(), 1e-12);
  EXPECT_LT((schedule.sample(1.9, home).q - third.sample(1.9).q).norm(), 1e-12);
  EXPECT_THROW(schedule.replace({}, 2.), std::invalid_argument);
}
TEST(Motion, CupFollowsIncomingBallAndBrakesUprightWithinLimits)
{
  Arm arm(robot());
  const auto home = uprightHome();
  const Vec3 incoming(-3., .2, -5.);
  const auto motion = cupCatchTrajectory(
    arm, 2., 1.8, {home, Vec7::Zero(), Vec7::Zero()}, neutral(), incoming, .35, .4);
  ASSERT_TRUE(motion);
  ASSERT_EQ(motion->size(), 2u);
  const auto & approach = motion->front();
  const auto & brake = motion->back();
  const auto entry = approach.sample(brake.start);
  const auto braking = brake.sample(brake.start);
  const Vec3 velocity = (arm.jacobian(entry.q) * entry.dq).head<3>();
  EXPECT_GT(velocity.dot(incoming.normalized()), .1);
  EXPECT_LT((incoming - velocity).norm(), incoming.norm());
  EXPECT_LT((entry.q - braking.q).norm(), 1e-10);
  EXPECT_LT((entry.dq - braking.dq).norm(), 1e-10);
  EXPECT_LT((entry.ddq - braking.ddq).norm(), 1e-10);
  const auto stopped = brake.sample(brake.start + brake.duration);
  EXPECT_LT(stopped.dq.norm(), 1e-10);
  EXPECT_LT(stopped.ddq.norm(), 1e-10);
  for (const auto & segment : *motion) {
    EXPECT_TRUE(segment.feasible(arm));
    for (int i = 0; i <= 100; ++i) {
      const auto pose = arm.pose(segment.sample(segment.start + segment.duration * i / 100.).q);
      const double tilt = segment.start == approach.start ? .08 + .36 * (1. - i / 100.) : .08;
      EXPECT_GE(pose.M(2, 2), std::cos(tilt));
      EXPECT_GE(pose.p.z(), .25);
    }
  }
  EXPECT_FALSE(cupCatchTrajectory(
    arm, 0., .01, {home, Vec7::Zero(), Vec7::Zero()}, home + Vec7::Constant(.5), incoming, 1., .4));
  EXPECT_THROW(
    cupCatchTrajectory(
      arm, 0., 1., {home, Vec7::Zero(), Vec7::Zero()}, home, Vec3::Zero(), .35, .4),
    std::invalid_argument);
}
TEST(Filter, MatchesPythonOnNoisyObservations)
{
  Reference reference;
  auto * observations = member(reference.data, "filter");
  BallFilter filter;
  for (size_t i = 0; i < json_object_array_length(observations); ++i) {
    auto * observation = json_object_array_get_idx(observations, i);
    auto state = filter.observe(
      vector<3>(member(observation, "point")), json_object_get_double(member(observation, "time")));
    auto * expected = member(observation, "state");
    ASSERT_EQ(state.has_value(), expected != nullptr);
    if (state) EXPECT_LT((*state - vector<6>(expected)).norm(), 1e-9);
  }
}
TEST(Filter, OcclusionOutlierAndNonfiniteData)
{
  BallFilter filter;
  for (int i = 0; i < 10; ++i) {
    const double t = i * .1 / 9;
    filter.observe(Vec3(1, 0, 2) + .5 * gravity * t * t, t);
  }
  EXPECT_FALSE(filter.observe(Vec3(5, 0, 2), .11));
  EXPECT_FALSE(filter.predict(.12));
  EXPECT_FALSE(filter.observe(Vec3(1, 0, 2), .5));
  EXPECT_FALSE(filter.observe(Vec3(NAN, 0, 2), .51));
}
TEST(Motion, MatchesPythonAtMultipleTimes)
{
  Reference reference;
  auto * motion = member(reference.data, "motion");
  Quintic trajectory(
    json_object_get_double(member(motion, "start")),
    json_object_get_double(member(motion, "duration")),
    {vector<7>(member(motion, "q")), vector<7>(member(motion, "dq")),
     vector<7>(member(motion, "ddq"))},
    vector<7>(member(motion, "target")));
  auto * samples = member(motion, "samples");
  for (size_t i = 0; i < json_object_array_length(samples); ++i) {
    auto * sample = json_object_array_get_idx(samples, i);
    const auto state = trajectory.sample(json_object_get_double(member(sample, "time")));
    auto * expected = member(sample, "state");
    EXPECT_LT((state.q - vector<7>(json_object_array_get_idx(expected, 0))).norm(), 1e-10);
    EXPECT_LT((state.dq - vector<7>(json_object_array_get_idx(expected, 1))).norm(), 1e-10);
    EXPECT_LT((state.ddq - vector<7>(json_object_array_get_idx(expected, 2))).norm(), 1e-10);
  }
}
TEST(Motion, ReplanningPreservesPositionVelocityAcceleration)
{
  Quintic first(0, 1, {Vec7::Zero(), Vec7::Zero(), Vec7::Zero()}, Vec7::Ones());
  const auto middle = first.sample(.4);
  Quintic second(.4, .8, middle, Vec7::Constant(.5));
  const auto start = second.sample(.4), finish = second.sample(1.2);
  EXPECT_LT((start.q - middle.q).norm(), 1e-12);
  EXPECT_LT((start.dq - middle.dq).norm(), 1e-12);
  EXPECT_LT((start.ddq - middle.ddq).norm(), 1e-12);
  EXPECT_LT((finish.q - Vec7::Constant(.5)).norm(), 1e-10);
  EXPECT_LT(finish.dq.norm(), 1e-10);
  EXPECT_LT(finish.ddq.norm(), 1e-10);
}
TEST(Motion, RejectsInvalidAndUnreachableTiming)
{
  Arm arm(robot());
  const auto q = neutral();
  EXPECT_THROW(Quintic(0, 0, {q, Vec7::Zero(), Vec7::Zero()}, q), std::invalid_argument);
  EXPECT_THROW(
    Quintic(0, 1, {q, Vec7::Zero(), Vec7::Zero()}, Vec7::Constant(NAN)), std::invalid_argument);
  EXPECT_TRUE(
    Quintic(0, 1, {q, Vec7::Zero(), Vec7::Zero()}, q + Vec7::Constant(.01)).feasible(arm));
  EXPECT_FALSE(
    Quintic(0, .02, {q, Vec7::Zero(), Vec7::Zero()}, q + Vec7::Constant(.5)).feasible(arm));
}
TEST(Arm, MatchesPythonFKMassAndForcesIncludingBracket)
{
  Reference reference;
  Arm arm(robot());
  auto * cases = member(reference.data, "arm");
  for (size_t i = 0; i < json_object_array_length(cases); ++i) {
    auto * c = json_object_array_get_idx(cases, i);
    const auto q = vector<7>(member(c, "q"));
    const auto pose = arm.pose(q);
    Vec3 position(pose.p.x(), pose.p.y(), pose.p.z());
    EXPECT_LT((position - vector<3>(member(c, "position"))).norm(), 1e-10);
    const auto mass = arm.mass(q);
    EXPECT_GT(Eigen::SelfAdjointEigenSolver<Mat7>(mass).eigenvalues().minCoeff(), 0);
    EXPECT_LT((arm.forces(q, Vec7::Constant(.05)) - vector<7>(member(c, "forces"))).norm(), 1e-9);
    for (int r = 0; r < 7; ++r)
      EXPECT_LT(
        (mass.row(r).transpose() - vector<7>(json_object_array_get_idx(member(c, "mass"), r)))
          .norm(),
        1e-9);
    for (int r = 0; r < 3; ++r)
      for (int col = 0; col < 3; ++col)
        EXPECT_NEAR(
          pose.M(r, col), vector<3>(json_object_array_get_idx(member(c, "rotation"), r))[col],
          1e-10);
  }
}
TEST(Arm, GravityMatchesIndependentPotentialGradient)
{
  const auto xml = robot();
  Arm arm(xml);
  urdf::Model model;
  ASSERT_TRUE(model.initString(xml));
  auto potential = [&](const Vec7 & q) {
    std::map<std::string, double> angles;
    for (int i = 0; i < 7; ++i) angles[arm.names[i]] = q[i];
    double energy = 0;
    std::function<void(urdf::LinkConstSharedPtr, KDL::Frame)> visit;
    visit = [&](urdf::LinkConstSharedPtr link, KDL::Frame transform) {
      if (link->inertial) {
        const auto & p = link->inertial->origin.position;
        const auto cog = transform * KDL::Vector(p.x, p.y, p.z);
        energy += link->inertial->mass * 9.81 * cog.z();
      }
      for (const auto & j : link->child_joints) {
        const auto & p = j->parent_to_joint_origin_transform;
        KDL::Frame origin(
          KDL::Rotation::Quaternion(p.rotation.x, p.rotation.y, p.rotation.z, p.rotation.w),
          KDL::Vector(p.position.x, p.position.y, p.position.z));
        if (j->type == urdf::Joint::REVOLUTE)
          origin = origin * KDL::Frame(KDL::Rotation::Rot(
                              KDL::Vector(j->axis.x, j->axis.y, j->axis.z), angles[j->name]));
        visit(model.getLink(j->child_link_name), transform * origin);
      }
    };
    visit(model.getRoot(), KDL::Frame::Identity());
    return energy;
  };
  const auto q = neutral();
  Vec7 gradient;
  for (int i = 0; i < 7; ++i) {
    Vec7 delta = Vec7::Zero();
    delta[i] = 1e-6;
    gradient[i] = (potential(q + delta) - potential(q - delta)) / 2e-6;
  }
  EXPECT_LT((arm.forces(q, Vec7::Zero()) - gradient).norm(), 1e-7);
}
TEST(Arm, InversePoseAndJointDependentSpeedLimits)
{
  Arm arm(robot());
  const auto q = neutral();
  const auto pose = arm.pose(q);
  EXPECT_NEAR(pose.M(2, 2), 1, 1e-12);
  const Vec3 target(.58, .04, .73);
  const auto solution = arm.inverse(target, pose.M, q);
  ASSERT_TRUE(solution);
  const auto result = arm.pose(*solution);
  EXPECT_LT((Vec3(result.p.x(), result.p.y(), result.p.z()) - target).norm(), .002);
  const auto [lo, hi] = arm.speedLimits(arm.upper);
  EXPECT_NEAR(hi.norm(), 0, 1e-12);
  EXPECT_LE(lo.maxCoeff(), 0);
}
TEST(Arm, StartingPoseValidation)
{
  Arm arm(robot());
  arm.validatePose(neutral());
  EXPECT_THROW(arm.validatePose(Vec7::Zero()), std::invalid_argument);
  auto q = neutral();
  q[4] = .5;
  EXPECT_THROW(arm.validateHome(q), std::invalid_argument);
  EXPECT_THROW(Arm("<robot/>"), std::invalid_argument);
}
TEST(Arm, HomeCannotBeReplacedWithAnUprightPreparedPose)
{
  Arm arm(robot());
  arm.validateHome(uprightHome());
  EXPECT_THROW(arm.validateHome(neutral()), std::invalid_argument);
  auto prepared = uprightHome();
  prepared[0] = .1;
  arm.validatePose(prepared);
  EXPECT_THROW(arm.validateHome(prepared), std::invalid_argument);
  EXPECT_THROW(arm.validateHome(Vec7::Constant(NAN)), std::invalid_argument);
  EXPECT_GT(arm.pose(uprightHome()).p.z(), 1.1);
}

TEST(Stereo, DistantDepthHasHigherUncertaintyAndOffsetRayCorrelation)
{
  Stereo stereo(800., 640., 480., .3, Vec3(-1., .75, 1.2));
  const auto near = stereo.observationNoise(Vec3(1., 0., .3));
  const auto far = stereo.observationNoise(Vec3(9., 0., .3));
  EXPECT_GT(far(0, 0), 100 * near(0, 0));
  EXPECT_LT(near(0, 1), 0.);
  EXPECT_LT(near(0, 2), 0.);
  EXPECT_GT(near.determinant(), 0.);
  EXPECT_THROW(stereo.observationNoise(Vec3(-2., 0., 0.)), std::invalid_argument);
}
