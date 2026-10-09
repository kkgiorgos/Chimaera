#include "ball_catching_robot/core.hpp"

#include <gtest/gtest.h>
#include <json-c/json.h>
#include <urdf/model.h>

#include <Eigen/Eigenvalues>
#include <fstream>
#include <functional>
#include <map>
#include <opencv2/imgproc.hpp>

using namespace ball_catching;
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
TEST(Flight, DragIntegrationAndVisualIdentification)
{
  Vec6 initial;
  initial << 12., -.1, 2.5, -25., .5, 1.;
  const auto noDrag = propagateBall(initial, .2, 0.);
  EXPECT_LT(
    (noDrag.head<3>() - initial.head<3>() - initial.tail<3>() * .2 - .5 * gravity * .04).norm(),
    1e-10);
  const auto withDrag = propagateBall(initial, .2, .022);
  EXPECT_LT(withDrag.tail<3>().norm(), noDrag.tail<3>().norm());
  DragFilter filter;
  std::optional<Vec6> state;
  for (int i = 0; i <= 50; ++i) {
    const double t = i / 90.;
    const auto truth = propagateBall(initial, t, .022);
    state = filter.observe(truth.head<3>(), t);
  }
  ASSERT_TRUE(state);
  EXPECT_NEAR(filter.drag(), .022, .003);
  EXPECT_LT((state->tail<3>() - propagateBall(initial, 50. / 90., .022).tail<3>()).norm(), .2);
  EXPECT_LT((*filter.predict(.6) - propagateBall(initial, .6, .022).head<3>()).norm(), .025);
  EXPECT_FALSE(filter.observe(Vec3::Constant(NAN), .7));
  EXPECT_FALSE(filter.predict(.1));
  EXPECT_THROW(propagateBall(initial, -.1, .02), std::invalid_argument);
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

TEST(Flight, VisualGroundReversalReacquiresWithoutKnowingRestitution)
{
  DragFilter filter;
  Vec6 state;
  state << 2., .1, .7, -2., 0., -3.;
  double time = 0.;
  std::optional<Vec6> estimate;
  for (int i = 0; i < 16; ++i) {
    estimate = filter.observe(state.head<3>(), time);
    state = propagateBall(state, .01, 0.);
    time += .01;
  }
  ASSERT_TRUE(estimate);
  ASSERT_LT((*estimate)[5], -1.);
  // Feed a measured rebound, using an arbitrary normal restitution that is
  // never passed to the filter. Its contact model remains continuous flight.
  state.z() = .04;
  state[5] = 3.2;
  bool lostTrack = false;
  for (int i = 0; i < 14; ++i) {
    estimate = filter.observe(state.head<3>(), time);
    lostTrack |= !estimate;
    state = propagateBall(state, .01, 0.);
    time += .01;
  }
  EXPECT_TRUE(lostTrack);
  EXPECT_GT(filter.resets(), 0u);
  ASSERT_TRUE(estimate);
  EXPECT_NEAR((*estimate)[3], -2., .15);
  EXPECT_NEAR((*estimate)[5], state[5] + .0981, .15);
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
