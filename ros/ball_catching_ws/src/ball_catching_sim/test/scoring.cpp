#include <cassert>
#include "ball_catching_sim/scoring.hpp"

int main() {
  using ball_catching::contained;
  assert(contained(0, 0, .06, .12, .14, .0335));
  // Resting at a polygon corner is valid, even beyond the inscribed circle.
  assert(contained(.083 * std::cos(std::acos(-1.) / 20),
                   .083 * std::sin(std::acos(-1.) / 20), .06, .12, .14, .0335));
  assert(!contained(.095, 0, .06, .12, .14, .0335));
  assert(!contained(0, 0, .12, .12, .14, .0335));
  assert(!contained(0, 0, .02, .12, .14, .0335));
  ball_catching::Retention retention;
  assert(!retention.Update(true, 1., 1.));
  assert(!retention.Update(true, 1.9, 1.));
  assert(!retention.Update(false, 1.95, 1.));
  assert(!retention.Update(true, 2., 1.));
  assert(!retention.Update(true, 2.9, 1.));
  assert(retention.Update(true, 3., 1.));
}
