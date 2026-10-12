#include "ball_catching_sim/scoring.hpp"

#include <cassert>
#include <initializer_list>

int main()
{
  using ball_catching::contained;
  assert(contained(0, 0, .06, .12, .14, .0335));
  // Resting at a polygon corner is valid, even beyond the inscribed circle.
  assert(contained(
    .083 * std::cos(std::acos(-1.) / 20), .083 * std::sin(std::acos(-1.) / 20), .06, .12, .14,
    .0335));
  assert(!contained(.095, 0, .06, .12, .14, .0335));
  assert(!contained(0, 0, .12, .12, .14, .0335));
  assert(!contained(0, 0, .02, .12, .14, .0335));
  using ball_catching::DeadlineScore;
  DeadlineScore score(2.);
  // Early containment never ends the experiment.
  assert(!score.Update(true, 1.1, 1.));
  assert(!score.Update(true, 2.9, 1.));
  // Dropping the ball before the deadline must overturn the earlier catch.
  const auto missed = score.Update(false, 3., 1.);
  assert(missed && !missed->success && missed->containedDuration == 0.);
  assert(!missed->captureTime);
  DeadlineScore lateCatch(2.);
  assert(!lateCatch.Update(false, 2.9, 1.));
  const auto caught = lateCatch.Update(true, 3., 1.);
  assert(caught && caught->success && caught->containedDuration == 0.);
  assert(*caught->captureTime == 3.);
  DeadlineScore recapture(2.);
  assert(!recapture.Update(true, 1.2, 1.));
  assert(!recapture.Update(false, 1.8, 1.));
  assert(!recapture.Update(true, 2.5, 1.));
  const auto held = recapture.Update(true, 3., 1.);
  assert(held && held->success && held->containedDuration == .5);
  assert(*held->captureTime == 2.5);
  DeadlineScore shortRun(.013);
  assert(!shortRun.Update(false, 1.012, 1.));
  assert(shortRun.Update(false, 1.013, 1.));
  for (double duration : {0., -1., double(NAN), double(INFINITY)}) {
    bool rejected = false;
    try {
      DeadlineScore invalid(duration);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    assert(rejected);
  }
}
