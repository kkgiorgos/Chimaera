"""Independent flight-geometry checks; no Gazebo or robot state required."""
import math

import pytest

from ball_catching_sim.flight import aimed_throw, drag_factor, court_net_height


def test_no_drag_matches_projectile_equations():
    position, target, speed = (10., -.2, 2.), (.5, .1, .8), 18.
    throw = aimed_throw(position, target, speed, drag=0)
    t = throw.flight_time
    predicted = [position[i] + speed * throw.direction[i] * t for i in range(3)]
    predicted[2] -= 9.81 * t * t / 2
    assert predicted == pytest.approx(target, abs=5e-5)
    assert sum(v * v for v in throw.direction) == pytest.approx(1.)


def test_full_court_drag_loses_speed_and_clears_net():
    throw = aimed_throw((23.77, 0., 2.5), (.5, .04, .9), 40., net_x=11.885)
    assert throw.path[-1][1:] == pytest.approx((.5, .04, .9), abs=.002)
    assert 0 < throw.arrival_speed < 40.
    assert all(sample[3] > .0335 for sample in throw.path)
    near_net = min(throw.path, key=lambda sample: abs(sample[1] - 11.885))
    assert near_net[3] > .914 + .0335


def test_impossible_slow_court_throw_is_not_a_robot_miss():
    with pytest.raises(ValueError, match='no floor/net-clearing trajectory'):
        aimed_throw((23.77, 0., 2.5), (.5, 0., .9), 3., net_x=11.885)


def test_net_can_invalidate_an_otherwise_reachable_throw():
    aimed_throw((5., 0., 1.), (.5, 0., .5), 30.)
    with pytest.raises(ValueError, match='no floor/net-clearing trajectory'):
        aimed_throw((5., 0., 1.), (.5, 0., .5), 30., net_x=2.5, net_height=100.)


@pytest.mark.parametrize('options', [dict(speed=math.nan), dict(drag=-1.),
                                    dict(step=0.), dict(maximum_time=0.),
                                    dict(target=(0., 0., 0.))])
def test_reject_invalid_flight_configuration(options):
    arguments = dict(position=(5., 0., 2.), target=(.5, 0., .8), speed=20.)
    arguments.update(options)
    with pytest.raises(ValueError):
        aimed_throw(**arguments)


def test_drag_parameter_validation():
    assert drag_factor(0) == 0
    with pytest.raises(ValueError):
        drag_factor(air_density=0)


def test_court_geometry_rejects_net_bypass_and_wrong_side():
    assert court_net_height((23.77, 0, 2.5), (.5, 0, .8)) > .914
    with pytest.raises(ValueError, match='opposite side'):
        court_net_height((.5, 0, 2.5), (.5, 0, .8))
    with pytest.raises(ValueError, match='net span'):
        court_net_height((23.77, 10, 2.5), (.5, 0, .8))


def test_vertical_and_near_vertical_gentle_throws():
    vertical = aimed_throw((.5, 0, 1.4), (.5, 0, .8), 1., drag=0)
    assert vertical.direction == (0., 0., -1.)
    expected_time = (math.sqrt(1+2*9.81*.6)-1)/9.81
    assert vertical.flight_time == pytest.approx(expected_time, abs=5e-5)
    assert vertical.arrival_speed == pytest.approx(1+9.81*expected_time, abs=.001)
    diagonal = aimed_throw((.5, 0, 1.4), (.51, -.02, .8), 1.)
    assert diagonal.path[-1][1:] == pytest.approx((.51, -.02, .8), abs=.002)
    assert diagonal.direction[2] < -.9
    with pytest.raises(ValueError, match='distinct'):
        aimed_throw((.5, 0, .8), (.5, 0, .8), 1.)


def test_lofted_toss_extends_physical_flight_without_higher_arrival_energy():
    origin, target = (.5, 0, 1.1), (.51, -.02, .8)
    low = aimed_throw(origin, target, 1., drag=0., arc='low')
    high = aimed_throw(origin, target, 1., drag=0., arc='high')
    assert low.direction[2] < 0 < high.direction[2]
    assert high.flight_time > low.flight_time + .15
    assert high.arrival_speed == pytest.approx(low.arrival_speed, abs=.001)
    assert high.path[-1][1:] == pytest.approx(target, abs=.002)
    vertical = aimed_throw(origin, (.5, 0, .8), 1., arc='high')
    assert vertical.direction == (0., 0., 1.)
    assert .36 < vertical.flight_time < .38
    assert 2.5 < vertical.arrival_speed < 2.7


def test_two_bounce_practice_shot_clears_real_court_and_arrives_gently():
    from ball_catching_sim.flight import bounced_throw, BALL_RADIUS
    throw = bounced_throw((23.77, 0, 2.5), (.5, .02, .8), 14.)
    assert len(throw.bounce_events) == 2
    assert all(BALL_RADIUS < event[1] < 11.885-BALL_RADIUS for event in throw.bounce_events)
    assert throw.bounce_events[0][1] > throw.bounce_events[1][1] > .5
    assert throw.path[-1][1:] == pytest.approx((.5, .02, .8), abs=.002)
    assert 3 < throw.arrival_speed < 3.3
    near_net = min(throw.path, key=lambda s: abs(s[1]-11.885))
    assert near_net[3] > .914+BALL_RADIUS
    assert all(s[3] >= BALL_RADIUS-1e-5 for s in throw.path)
    assert 3.1 < throw.flight_time < 3.4


@pytest.mark.parametrize('options', [dict(bounces=0), dict(bounces=5),
                                    dict(restitution=1.1), dict(tangent_ratio=0),
                                    dict(speed=math.nan)])
def test_rejects_invalid_or_energy_adding_court_impact(options):
    from ball_catching_sim.flight import bounced_throw
    args = dict(position=(23.77, 0, 2.5), target=(.5, 0, .8), speed=14.)
    args.update(options)
    with pytest.raises(ValueError):
        bounced_throw(**args)
