#include "ball_catching_robot/core.hpp"
#include <Eigen/Dense>
#include <kdl/chain.hpp>
#include <kdl/chainfksolverpos_recursive.hpp>
#include <kdl/chainjnttojacsolver.hpp>
#include <kdl/chaindynparam.hpp>
#include <urdf/model.h>
#include <algorithm>
#include <functional>
#include <set>
#include <stdexcept>

namespace ball_catching {
namespace {
KDL::Frame frame(const urdf::Pose &p) {
  return {KDL::Rotation::Quaternion(p.rotation.x, p.rotation.y, p.rotation.z, p.rotation.w),
          KDL::Vector(p.position.x, p.position.y, p.position.z)};
}
KDL::JntArray joints(const Vec7 &q) { KDL::JntArray result(7); result.data = q; return result; }
}
struct Arm::Impl {
  KDL::Chain chain;
  std::unique_ptr<KDL::ChainFkSolverPos_recursive> fk;
  std::unique_ptr<KDL::ChainJntToJacSolver> jacobian;
  std::unique_ptr<KDL::ChainDynParam> dynamics;
};

Arm::Arm(const std::string &xml) : impl_(std::make_unique<Impl>()) {
  urdf::Model model;
  if (!model.initString(xml)) throw std::invalid_argument("Invalid robot URDF");
  std::vector<urdf::JointConstSharedPtr> path;
  std::set<std::string> pathNames;
  gripper = bool(model.getLink("grasp_center"));
  auto link = model.getLink(gripper ? "grasp_center" : "cup_rim");
  while (link && link->name != "fr3_link0") {
    auto joint = link->parent_joint;
    if (!joint) break;
    path.push_back(joint);
    pathNames.insert(joint->name);
    link = model.getLink(joint->parent_link_name);
  }
  if (!link || link->name != "fr3_link0") throw std::invalid_argument("Missing FR3-to-tool chain");
  std::function<KDL::RigidBodyInertia(urdf::LinkConstSharedPtr)> body;
  body = [&](urdf::LinkConstSharedPtr child) {
    auto inertia = KDL::RigidBodyInertia::Zero();
    if (child->inertial) {
      const auto &i = *child->inertial;
      const auto origin = frame(i.origin);
      Eigen::Matrix3d tensor, rotation;
      tensor << i.ixx, i.ixy, i.ixz, i.ixy, i.iyy, i.iyz, i.ixz, i.iyz, i.izz;
      for (int r = 0; r < 3; ++r) for (int c = 0; c < 3; ++c) rotation(r, c) = origin.M(r, c);
      tensor = (rotation * tensor * rotation.transpose()).eval();
      inertia = KDL::RigidBodyInertia(i.mass, origin.p, KDL::RotationalInertia(
          tensor(0, 0), tensor(1, 1), tensor(2, 2), tensor(0, 1), tensor(0, 2), tensor(1, 2)));
    }
    // Include fixed side branches such as the rigid mounting bracket.
    for (const auto &joint : child->child_joints)
      if (
        !pathNames.count(joint->name) &&
        (joint->type == urdf::Joint::FIXED || (gripper && joint->type == urdf::Joint::PRISMATIC))) {
        auto origin = frame(joint->parent_to_joint_origin_transform);
        // Finger travel changes their contribution only slightly. Include both
        // fingers at nominal open width in the seven-joint arm dynamics model.
        if (joint->type == urdf::Joint::PRISMATIC)
          origin = origin * KDL::Frame(KDL::Vector(
                              joint->axis.x * .04, joint->axis.y * .04, joint->axis.z * .04));
        inertia = inertia + origin * body(model.getLink(joint->child_link_name));
      }
    return inertia;
  };
  std::reverse(path.begin(), path.end());
  for (const auto &joint : path) {
    const auto inertia = body(model.getLink(joint->child_link_name));
    const auto origin = frame(joint->parent_to_joint_origin_transform);
    if (joint->type == urdf::Joint::REVOLUTE) {
      if (names.size() >= 7 || !joint->limits) throw std::invalid_argument("Expected seven limited FR3 joints");
      impl_->chain.addSegment(KDL::Segment(joint->name + "_origin", KDL::Joint(KDL::Joint::Fixed), origin));
      impl_->chain.addSegment(KDL::Segment(joint->child_link_name,
          KDL::Joint(joint->name, KDL::Vector::Zero(), KDL::Vector(joint->axis.x, joint->axis.y, joint->axis.z),
                     KDL::Joint::RotAxis), KDL::Frame::Identity(), inertia));
      const auto i = names.size();
      names.push_back(joint->name);
      lower[i] = joint->limits->lower; upper[i] = joint->limits->upper;
      velocity[i] = joint->limits->velocity; effort[i] = joint->limits->effort;
    } else if (joint->type == urdf::Joint::FIXED) {
      impl_->chain.addSegment(KDL::Segment(joint->child_link_name, KDL::Joint(KDL::Joint::Fixed), origin, inertia));
    } else throw std::invalid_argument("Only fixed/revolute arm joints are supported");
  }
  if (names.size() != 7) throw std::invalid_argument("Expected seven FR3 joints");
  impl_->fk = std::make_unique<KDL::ChainFkSolverPos_recursive>(impl_->chain);
  impl_->jacobian = std::make_unique<KDL::ChainJntToJacSolver>(impl_->chain);
  impl_->dynamics = std::make_unique<KDL::ChainDynParam>(impl_->chain, KDL::Vector(0, 0, -9.81));
}
Arm::~Arm() = default;
KDL::Frame Arm::pose(const Vec7 &q) const {
  KDL::Frame result;
  if (impl_->fk->JntToCart(joints(q), result) < 0) throw std::runtime_error("FK failed");
  return result;
}
std::optional<Vec7> Arm::inverse(const Vec3 &position, const KDL::Rotation &rotation,
                                const Vec7 &seed, int iterations) const {
  const KDL::Frame target(rotation, KDL::Vector(position.x(), position.y(), position.z()));
  Vec7 q = seed;
  for (int k = 0; k < iterations; ++k) {
    const auto error = KDL::diff(pose(q), target);
    Vec6 delta;
    for (int i = 0; i < 6; ++i) delta[i] = error[i];
    if (delta.head<3>().norm() < .002 && delta.tail<3>().norm() < .015) return q;
    KDL::Jacobian jac(7);
    if (impl_->jacobian->JntToJac(joints(q), jac) < 0) return {};
    const auto &j = jac.data;
    Vec7 step = j.transpose() * (j * j.transpose() + .002 * .002 * Mat6::Identity()).ldlt().solve(delta);
    q += step * std::min(1., .18 / std::max(step.cwiseAbs().maxCoeff(), 1e-9));
    q = q.cwiseMax((lower.array() + .025).matrix()).cwiseMin((upper.array() - .025).matrix());
  }
  return {};
}
Vec7 Arm::forces(const Vec7 &q, const Vec7 &dq) const {
  KDL::JntArray g(7), c(7);
  if (impl_->dynamics->JntToGravity(joints(q), g) < 0 ||
      impl_->dynamics->JntToCoriolis(joints(q), joints(dq), c) < 0) throw std::runtime_error("Dynamics failed");
  return g.data + c.data;
}
Mat7 Arm::mass(const Vec7 &q) const {
  KDL::JntSpaceInertiaMatrix matrix(7);
  if (impl_->dynamics->JntToMass(joints(q), matrix) < 0) throw std::runtime_error("Mass calculation failed");
  return matrix.data;
}
Eigen::Matrix<double, 6, 7> Arm::jacobian(const Vec7 & q) const
{
  KDL::Jacobian result(7);
  if (impl_->jacobian->JntToJac(joints(q), result) < 0) throw std::runtime_error("Jacobian failed");
  return result.data;
}
std::pair<Vec7, Vec7> Arm::speedLimits(const Vec7 &q) const {
  // FR3 interface specification; position and direction affect speed bounds.
  const Vec7 offset((Vec7() << .6599, .2517, .2000, .3533, .5757, .4878, .4628).finished());
  const Vec7 deceleration((Vec7() << 6., 2.585, 3.5, 4., 17., 5.5, 17.).finished());
  Vec7 lo, hi;
  for (int i = 0; i < 7; ++i) {
    hi[i] = std::min(velocity[i], std::max(0., -offset[i] + std::sqrt(std::max(0., 2 * deceleration[i] * (upper[i] - q[i])))));
    lo[i] = -std::min(velocity[i], std::max(0., -offset[i] + std::sqrt(std::max(0., 2 * deceleration[i] * (q[i] - lower[i])))));
  }
  return {lo, hi};
}
void Arm::validatePose(const Vec7 &q) const {
  if (!q.allFinite() || (q.array() < lower.array() + .02).any() || (q.array() > upper.array() - .02).any())
    throw std::invalid_argument("initial_pose must be within joint limits with 0.02 rad margin");
}
Vec7 uprightHome(bool gripper) {
  if (!gripper)
    return (Vec7() << 0., 0., 0., -.14, 0., .50, .7853981633974483).finished();
  return (Vec7() << 0., -.7853981633974483, 0., -2.356194490192345, 0.,
          1.5707963267948966, .7853981633974483).finished();
}
void Arm::validateHome(const Vec7 &q) const {
  validatePose(q);
  if ((q - uprightHome(gripper)).cwiseAbs().maxCoeff() > 1e-8)
    throw std::invalid_argument("Robot must start from the fixed upright home pose");
}
}
