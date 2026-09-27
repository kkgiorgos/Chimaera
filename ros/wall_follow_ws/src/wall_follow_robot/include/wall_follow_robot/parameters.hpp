#pragma once
#include "wall_follow_robot/core.hpp"

// clang-format off
#define DOUBLE_PARAMS(X) \
  X(control_hz) X(target_distance) X(speed) X(kp) X(heading_gain) \
  X(max_yaw_rate) X(front_stop) X(scan_timeout) X(sector_start) \
  X(sector_end) X(fit_threshold)
#define INT_PARAMS(X) X(beam_stride) X(min_points)
// clang-format on
using wall_follow::Parameters;
