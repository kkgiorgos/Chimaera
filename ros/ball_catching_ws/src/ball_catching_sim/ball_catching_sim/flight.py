"""Host-only throw generation with gravity and quadratic drag (no robot inputs)."""
from dataclasses import dataclass
import math


GRAVITY = 9.81
BALL_RADIUS = 0.0335
BALL_MASS = 0.057
COURT_LENGTH = 23.77
COURT_WIDTH = 8.23
NET_HEIGHT = 0.914


def court_net_height(position, target):
    """Verify baseline-to-receiver geometry and conservatively clear the net."""
    if len(position) != 3 or len(target) != 3 or not all(math.isfinite(x) for x in (*position, *target)):
        raise ValueError('launch and target need three finite coordinates')
    if position[0] <= COURT_LENGTH/2 or target[0] >= COURT_LENGTH/2:
        raise ValueError('court throws must cross the net from the opposite side')
    fraction = (COURT_LENGTH/2 - position[0])/(target[0] - position[0])
    net_y = position[1] + fraction*(target[1] - position[1])
    if abs(net_y) + BALL_RADIUS > COURT_WIDTH/2:
        raise ValueError('throw would bypass the physical net span')
    return NET_HEIGHT + (1.07-NET_HEIGHT)*(2*(abs(net_y)+BALL_RADIUS)/COURT_WIDTH)**2


def drag_factor(coefficient=0.55, air_density=1.225):
    """Acceleration coefficient k in a = g - k |v| v, in inverse metres."""
    if not math.isfinite(coefficient) or coefficient < 0:
        raise ValueError('drag coefficient must be finite and nonnegative')
    if not math.isfinite(air_density) or air_density <= 0:
        raise ValueError('air density must be finite and positive')
    return .5 * air_density * coefficient * math.pi * BALL_RADIUS ** 2 / BALL_MASS


def _step(state, dt, drag):
    def rate(s):
        speed = math.hypot(s[2], s[3])
        return s[2], s[3], -drag * speed * s[2], -GRAVITY - drag * speed * s[3]

    a = rate(state)
    b = rate(tuple(s + dt * r / 2 for s, r in zip(state, a)))
    c = rate(tuple(s + dt * r / 2 for s, r in zip(state, b)))
    d = rate(tuple(s + dt * r for s, r in zip(state, c)))
    return tuple(s + dt * (u + 2 * v + 2 * w + x) / 6
                 for s, u, v, w, x in zip(state, a, b, c, d))


def _crossing(height, speed, angle, distance, drag, step, maximum_time,
              keep_path=False):
    state = (0., height, speed * math.cos(angle), speed * math.sin(angle))
    path = [(0., *state)] if keep_path else []
    time = 0.
    while time < maximum_time:
        dt = min(step, maximum_time - time)
        previous = state
        state = _step(state, dt, drag)
        if state[0] >= distance:
            fraction = (distance - previous[0]) / (state[0] - previous[0])
            state = tuple(a + fraction * (b - a) for a, b in zip(previous, state))
            time += dt * fraction
            if keep_path:
                path.append((time, *state))
            return time, state, path
        time += dt
        if keep_path:
            path.append((time, *state))
        if state[2] < 1e-5:
            break
    return None


@dataclass(frozen=True)
class Throw:
    direction: tuple
    flight_time: float
    arrival_velocity: tuple
    path: tuple
    bounce_events: tuple = ()

    @property
    def arrival_speed(self):
        return math.sqrt(sum(v * v for v in self.arrival_velocity))


def aimed_throw(position, target, speed, *, drag=None, net_x=None,
                net_height=NET_HEIGHT, step=.005, maximum_time=8., arc='low'):
    """Find a valid sampled low or high arc at a fixed launch-speed magnitude.

    Raises ValueError if no arc reaches the target before floor/net contact.
    This tests flight geometry only, never the robot's dynamic ability to catch.
    The caller must separately verify target reachability and lateral net span.
    """
    if len(position) != 3 or len(target) != 3:
        raise ValueError('launch and target positions need three coordinates')
    if arc not in ('low', 'high'):
        raise ValueError('arc must be low or high')
    if not all(math.isfinite(x) for x in (*position, *target, speed)) or speed <= 0:
        raise ValueError('positions must be finite and speed positive')
    drag = drag_factor() if drag is None else drag
    if not math.isfinite(drag) or drag < 0 or not math.isfinite(step) or step <= 0:
        raise ValueError('drag must be nonnegative and integration step positive')
    if not math.isfinite(maximum_time) or maximum_time <= 0:
        raise ValueError('maximum flight time must be positive')
    if not math.isfinite(net_height) or net_height <= 0:
        raise ValueError('net height must be positive')
    if position[2] <= BALL_RADIUS or target[2] <= BALL_RADIUS:
        raise ValueError('launch and target must clear the floor')
    dx, dy = target[0] - position[0], target[1] - position[1]
    distance = math.hypot(dx, dy)
    if distance < 1e-8:
        if abs(target[2]-position[2]) < 1e-8:
            raise ValueError('launch and target must be distinct')
        if net_x is not None and not math.isfinite(net_x):
            raise ValueError('net plane must be finite')
        if net_x is not None and abs(net_x-position[0]) < .01+BALL_RADIUS and min(position[2], target[2]) <= net_height+BALL_RADIUS:
            raise ValueError('vertical trajectory would intersect the net')
        # An overhead toss is a useful low-energy baseline. This is still free
        # physical flight; only the host's direction solver handles this case.
        target_side = -1. if target[2] < position[2] else 1.
        direction_z = 1. if arc == 'high' else target_side
        state = (0., position[2], 0., direction_z*speed)
        path = [(0., *position)]
        time = 0.
        while time < maximum_time:
            dt = min(step, maximum_time-time)
            previous = state
            state = _step(state, dt, drag)
            if target_side*(previous[1]-target[2]) <= 0 <= target_side*(state[1]-target[2]):
                fraction = (target[2]-previous[1])/(state[1]-previous[1])
                arrival_z = previous[3]+fraction*(state[3]-previous[3])
                time += dt*fraction
                path.append((time, target[0], target[1], target[2]))
                return Throw((0., 0., direction_z), time, (0., 0., arrival_z), tuple(path))
            time += dt
            path.append((time, position[0], position[1], state[1]))
            if state[1] <= BALL_RADIUS:
                break
        raise ValueError('no floor-clearing vertical trajectory reaches the target')
    horizontal = (dx / distance, dy / distance)
    net_distance = None
    if net_x is not None:
        if not math.isfinite(net_x) or abs(dx) < 1e-9:
            raise ValueError('net plane must be finite and crossable')
        fraction = (net_x - position[0]) / dx
        if 0 < fraction < 1:
            net_distance = fraction * distance

    previous, best = None, None
    for degrees in (-89.99, -89.9, -89.5, *range(-88, 90, 2), 89.5, 89.9, 89.99):
        angle = math.radians(degrees)
        candidate = _crossing(position[2], speed, angle, distance, drag, step, maximum_time)
        if candidate is None:
            previous = None
            continue
        error = candidate[1][1] - target[2]
        if previous is not None and error * previous[1] <= 0:
            lower, upper = previous[0], angle
            lower_error = previous[1]
            for _ in range(28):
                middle = (lower + upper) / 2
                value = _crossing(position[2], speed, middle, distance, drag, step, maximum_time)
                if value is None:
                    break
                middle_error = value[1][1] - target[2]
                if middle_error * lower_error > 0:
                    lower, lower_error = middle, middle_error
                else:
                    upper = middle
            launch_angle = (lower + upper) / 2
            flight = _crossing(position[2], speed, launch_angle, distance, drag,
                               step, maximum_time, keep_path=True)
            if flight is not None and abs(flight[1][1] - target[2]) < .002:
                floor_clear = all(s[2] > BALL_RADIUS for s in flight[2])
                net_clear = True
                if net_distance is not None:
                    # Check both faces of the physical 20 mm net slab, including
                    # the ball radius, rather than its centre plane alone.
                    for along in (net_distance - .01 - BALL_RADIUS,
                                  net_distance + .01 + BALL_RADIUS):
                        if 0 < along < distance:
                            at_net = _crossing(position[2], speed, launch_angle, along,
                                               drag, step, maximum_time)
                            net_clear &= at_net is not None and at_net[1][1] > net_height + BALL_RADIUS
                if floor_clear and net_clear:
                    direction = (horizontal[0] * math.cos(launch_angle),
                                 horizontal[1] * math.cos(launch_angle), math.sin(launch_angle))
                    arrival = (horizontal[0] * flight[1][2], horizontal[1] * flight[1][2],
                               flight[1][3])
                    path = tuple((t, position[0] + horizontal[0] * x,
                                  position[1] + horizontal[1] * x, z)
                                 for t, x, z, _, _ in flight[2])
                    best = Throw(direction, flight[0], arrival, path)
                    if arc == 'low':
                        return best
        previous = angle, error
    if best is not None:
        return best
    raise ValueError('no floor/net-clearing trajectory reaches the target at this launch speed')


def bounced_throw(position, target, speed, *, bounces=2, restitution=.745,
                  tangent_ratio=.6, drag=None, net_x=COURT_LENGTH/2,
                  net_height=NET_HEIGHT, step=.005, maximum_time=12., arc='low'):
    """Host-only practice-shot solver with a passive, empirical court impact.

    Ground reflection scales normal velocity by restitution and horizontal
    velocity by tangent_ratio. Rotation/deformation are not explicitly modeled.
    Exactly `bounces` must occur on the receiving half before target crossing.
    """
    if len(position) != 3 or len(target) != 3:
        raise ValueError('launch and target positions need three coordinates')
    if bounces not in (1, 2, 3, 4):
        raise ValueError('court bounces must be between one and four')
    if not 0 < restitution <= 1 or not 0 < tangent_ratio <= 1:
        raise ValueError('court impact coefficients must be in (0, 1]')
    # Reuse the straight-flight input and lateral/net-span validation.
    if not all(math.isfinite(x) for x in (*position, *target, speed, restitution, tangent_ratio)) or speed <= 0:
        raise ValueError('positions and impact coefficients must be finite and speed positive')
    if arc not in ('low', 'high') or not all(math.isfinite(x) for x in (step, maximum_time, net_x, net_height)) or step <= 0 or maximum_time <= 0:
        raise ValueError('invalid bounce integration or arc')
    if position[2] <= BALL_RADIUS or target[2] <= BALL_RADIUS:
        raise ValueError('launch and target must clear the floor')
    net_height = max(net_height, court_net_height(position, target))
    drag = drag_factor() if drag is None else drag
    if not math.isfinite(drag) or drag < 0:
        raise ValueError('drag must be finite and nonnegative')
    dx, dy = target[0]-position[0], target[1]-position[1]
    distance = math.hypot(dx, dy)
    horizontal = (dx/distance, dy/distance)
    net_distance = (net_x-position[0])/horizontal[0]

    def integrate(angle, keep=False):
        state = (0., position[2], speed*math.cos(angle), speed*math.sin(angle))
        time, events = 0., []
        path = [(0., *position)] if keep else []
        while time < maximum_time:
            dt = min(step, maximum_time-time)
            previous = state
            state = _step(state, dt, drag)
            # All trajectories must clear both net faces before any bounce.
            for plane in (net_distance-.01-BALL_RADIUS, net_distance+.01+BALL_RADIUS):
                if previous[0] < plane <= state[0]:
                    f = (plane-previous[0])/(state[0]-previous[0])
                    if previous[1]+f*(state[1]-previous[1]) <= net_height+BALL_RADIUS:
                        return None
            if state[1] <= BALL_RADIUS and state[3] < 0:
                if len(events) >= bounces:
                    return None
                # Locate the physical plane event, then integrate the remainder.
                lower, upper = 0., dt
                for _ in range(18):
                    middle = (lower+upper)/2
                    if _step(previous, middle, drag)[1] > BALL_RADIUS:
                        lower = middle
                    else:
                        upper = middle
                hit_dt = (lower+upper)/2
                hit = _step(previous, hit_dt, drag)
                x = position[0]+horizontal[0]*hit[0]
                y = position[1]+horizontal[1]*hit[0]
                if not BALL_RADIUS < x < COURT_LENGTH/2-BALL_RADIUS or abs(y)+BALL_RADIUS > COURT_WIDTH/2:
                    return None
                events.append((time+hit_dt, x, y, BALL_RADIUS))
                if keep:
                    path.append(events[-1])
                state = _step((hit[0], BALL_RADIUS, tangent_ratio*hit[2], -restitution*hit[3]), dt-hit_dt, drag)
            if state[0] >= distance:
                if len(events) != bounces:
                    return None
                f = (distance-previous[0])/(state[0]-previous[0])
                crossing = tuple(a+f*(b-a) for a,b in zip(previous,state))
                end_time = time+dt*f
                if keep:
                    path.append((end_time, target[0], target[1], crossing[1]))
                return crossing[1]-target[2], end_time, crossing, tuple(path), tuple(events)
            time += dt
            if keep:
                path.append((time, position[0]+horizontal[0]*state[0], position[1]+horizontal[1]*state[0], state[1]))
        return None

    previous, solutions = None, []
    for degrees in range(-10, 891):
        angle = math.radians(degrees/10)
        flight = integrate(angle)
        if flight is None:
            previous = None
            continue
        if previous is not None and previous[1]*flight[0] <= 0:
            lo, hi, error = previous[0], angle, previous[1]
            for _ in range(25):
                middle = (lo+hi)/2
                value = integrate(middle)
                if value is None:
                    break
                if value[0]*error > 0:
                    lo, error = middle, value[0]
                else:
                    hi = middle
            launch_angle = (lo+hi)/2
            found = integrate(launch_angle, keep=True)
            if found is not None and abs(found[0]) < .002:
                velocity = (horizontal[0]*found[2][2], horizontal[1]*found[2][2], found[2][3])
                solutions.append(Throw((horizontal[0]*math.cos(launch_angle), horizontal[1]*math.cos(launch_angle), math.sin(launch_angle)),
                                       found[1], velocity, found[3], found[4]))
                if arc == 'low':
                    return solutions[0]
        previous = angle, flight[0]
    if solutions:
        return solutions[-1]
    raise ValueError('no net-clearing trajectory reaches the target after the specified court bounces')
