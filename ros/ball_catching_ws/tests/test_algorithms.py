import numpy as np
from ball_catching_robot.vision import Stereo, BallFilter, GRAVITY
from ball_catching_robot.motion import Quintic


def test_metric_stereo_without_known_ball_size():
    stereo = Stereo(400, 320, 240, 0.3, [-0.65, 0.15, 1.0])
    # World point [1.35, .05, 1.1], expressed in each calibrated optical frame.
    point = stereo.point((340, 220, 8), (280, 220, 8))
    np.testing.assert_allclose(point, [1.35, .05, 1.1])
    assert stereo.point((340, 220, 8), (340, 220, 8)) is None
    assert stereo.point((340, 220, 8), (280, 230, 8)) is None


def test_ballistic_velocity_recovery_from_observations():
    tracker = BallFilter()
    initial = np.array([2., -.2, 1.6])
    velocity = np.array([-4., .5, 1.])
    for t in np.linspace(0., .2, 19):
        p = initial + velocity * t + GRAVITY * t*t / 2
        state = tracker.observe(p, t)
    np.testing.assert_allclose(state[:3], p, atol=1e-8)
    np.testing.assert_allclose(state[3:], velocity + GRAVITY * t, atol=1e-8)
    future = .35
    np.testing.assert_allclose(tracker.predict(future),
                               initial + velocity * future + GRAVITY * future**2 / 2, atol=1e-8)
    assert tracker.observe(p, t) is None


def test_track_resets_after_occlusion_and_large_innovation():
    tracker = BallFilter()
    for t in np.linspace(0, .1, 10):
        tracker.observe(np.array([1., 0., 2.]) + GRAVITY * t*t / 2, t)
    assert tracker.observe(np.array([5., 0., 2.]), .11) is None
    assert tracker.state is None
    assert tracker.observe(np.array([1., 0., 2.]), .5) is None
    assert len(tracker.history) == 1


def test_replanned_motion_preserves_position_velocity_acceleration():
    q, dq, ddq = np.zeros(7), np.zeros(7), np.zeros(7)
    first = Quintic(0., 1., q, dq, ddq, np.ones(7))
    middle = first.sample(.4)
    second = Quintic(.4, .8, *middle, np.full(7, .5))
    for before, after in zip(middle, second.sample(.4)):
        np.testing.assert_allclose(before, after, atol=1e-12)
    end, speed, acceleration = second.sample(1.2)
    np.testing.assert_allclose(end, .5, atol=1e-10)
    np.testing.assert_allclose(speed, 0, atol=1e-10)
    np.testing.assert_allclose(acceleration, 0, atol=1e-10)
