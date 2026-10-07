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
  using ball_catching::physicallyGrasped;
  assert(physicallyGrasped(0, 0, 0, .01, .8, true, true, false));
  assert(!physicallyGrasped(0, 0, 0, .01, .8, true, false, false));
  assert(!physicallyGrasped(0, 0, 0, .01, .8, true, true, true));
  assert(!physicallyGrasped(0, 0, 0, 2., .8, true, true, false));
  assert(!physicallyGrasped(.1, 0, 0, .01, .8, true, true, false));
  assert(!physicallyGrasped(0, 0, 0, .01, .02, true, true, false));
  assert(!physicallyGrasped(NAN, 0, 0, .01, .8, true, true, false));
}
