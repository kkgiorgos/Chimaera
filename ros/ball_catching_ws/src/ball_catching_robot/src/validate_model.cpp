#include <fstream>
#include <iostream>

#include "ball_catching_robot/core.hpp"

int main(int argc, char ** argv)
{
  try {
    if (argc < 3)
      throw std::invalid_argument(
        "Usage: validate_model ROBOT_URDF Q1..Q7 | --home | --target X Y Z");
    std::ifstream file(argv[1]);
    if (!file) throw std::invalid_argument("Cannot read robot URDF");
    const std::string xml{std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
    ball_catching::Arm arm(xml);
    ball_catching::Vec7 q;
    if (
      std::string(argv[2]) == "--home" || std::string(argv[2]) == "--target" ||
      std::string(argv[2]) == "--upward-target" || std::string(argv[2]) == "--pitched-target") {
      const bool pitched = std::string(argv[2]) == "--pitched-target";
      if (pitched ? argc != 7 : (argc != 3 && argc != 6))
        throw std::invalid_argument("Invalid target arguments");
      const int offset = pitched ? 4 : 3;
      const ball_catching::Vec3 point =
        argc >= 6
          ? ball_catching::Vec3(
              std::stod(argv[offset]), std::stod(argv[offset + 1]), std::stod(argv[offset + 2]))
          : ball_catching::Vec3(.5, 0., .8);
      q << 0., -.7853981634, 0., -2.35619449, 0., 1.570796327, .7853981634;
      const double pitch = pitched                                     ? std::stod(argv[3])
                           : std::string(argv[2]) == "--upward-target" ? 90.
                                                                       : 0.;
      if (!std::isfinite(pitch) || pitch < 0 || pitch > 180)
        throw std::invalid_argument("Home pitch must be between 0 and 180 degrees");
      const auto rotation = KDL::Rotation::RotY((90. - pitch) * std::acos(-1.) / 180.);
      auto target = arm.inverse(point, rotation, q, 250);
      if (pitched) {
        // Configure an interior home posture instead of accepting the first IK
        // branch clamped against a wrist limit. This uses robot geometry only.
        double margin =
          target ? (*target - arm.lower).cwiseMin(arm.upper - *target).minCoeff() : -1.;
        for (double shoulder : {-1., 0., 1.})
          for (double elbow : {-1., 0., 1.})
            for (double wrist : {-1.5, 0., 1.5}) {
              auto seed = q;
              seed[0] = shoulder;
              seed[2] = elbow;
              seed[4] = wrist;
              const auto candidate = arm.inverse(point, rotation, seed, 300);
              if (!candidate) continue;
              const double candidateMargin =
                (*candidate - arm.lower).cwiseMin(arm.upper - *candidate).minCoeff();
              if (candidateMargin > margin + 1e-5) {
                target = candidate;
                margin = candidateMargin;
              }
            }
      }
      if (!target)
        throw std::invalid_argument("Target is outside the configured gripper workspace");
      arm.validatePose(*target);
      std::cout << '[';
      for (int i = 0; i < 7; ++i) std::cout << (i ? "," : "") << (*target)[i];
      std::cout << "]\n";
      return 0;
    }
    if (argc != 9) throw std::invalid_argument("Expected seven joint angles");
    for (int i = 0; i < 7; ++i) q[i] = std::stod(argv[i + 2]);
    arm.validatePose(q);
    return 0;
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
