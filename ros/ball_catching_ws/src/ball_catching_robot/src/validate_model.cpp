#include "ball_catching_robot/core.hpp"
#include <fstream>
#include <iostream>

int main(int argc, char **argv) {
  try {
    if (argc != 9) throw std::invalid_argument("Usage: validate_model ROBOT_URDF Q1 Q2 Q3 Q4 Q5 Q6 Q7");
    std::ifstream file(argv[1]);
    if (!file) throw std::invalid_argument("Cannot read robot URDF");
    const std::string xml{std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
    ball_catching::Arm arm(xml);
    ball_catching::Vec7 q;
    for (int i = 0; i < 7; ++i) q[i] = std::stod(argv[i + 2]);
    arm.validatePose(q);
    return 0;
  } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
