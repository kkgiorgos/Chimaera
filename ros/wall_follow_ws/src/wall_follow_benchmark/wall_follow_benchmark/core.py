"""ROS-independent right-wall estimator and bounded controller."""
import math
import numpy as np

DEFAULTS = dict(control_hz=20.0, target_distance=0.8, speed=0.35,
                kp=1.8, heading_gain=2.0, max_yaw_rate=1.2,
                front_stop=0.65, scan_timeout=0.3, beam_stride=1,
                sector_start=-110.0, sector_end=-40.0, min_points=6, fit_threshold=0.04)


def validate(p):
    for k in DEFAULTS:
        if not isinstance(p[k], (int, float)) or not math.isfinite(p[k]):
            raise ValueError(f'{k} must be finite')
    for k in ('control_hz', 'target_distance', 'speed', 'max_yaw_rate', 'front_stop', 'scan_timeout', 'fit_threshold'):
        if p[k] <= 0:
            raise ValueError(f'{k} must be positive')
    if p['kp'] < 0 or p['heading_gain'] < 0:
        raise ValueError('gains must be nonnegative')
    if not 1 <= p['control_hz'] <= 500:
        raise ValueError('control_hz must be in [1, 500]')
    if not -175 <= p['sector_start'] < p['sector_end'] <= -5:
        raise ValueError('wall sector must lie on the right: [-175, -5] degrees')
    if type(p['beam_stride']) is not int or p['beam_stride'] < 1:
        raise ValueError('beam_stride must be a positive integer')
    if type(p['min_points']) is not int or p['min_points'] < 3:
        raise ValueError('min_points must be an integer >= 3')


def command(ranges, angle_min, angle_increment, range_min, range_max, p):
    r = np.asarray(ranges, dtype=float)
    a = angle_min + np.arange(len(r)) * angle_increment
    valid = np.isfinite(r) & (r >= range_min) & (r <= range_max)
    front_mask = np.abs(a) < math.radians(20)
    front = r[valid & front_mask]
    # Positive infinity is a valid no-return; NaNs and out-of-range finite data are not.
    front_known = np.any((valid | np.isposinf(r)) & front_mask)
    clearance = float(np.min(front)) if len(front) else float('inf')
    if not front_known:
        return 0., 0., float('nan'), float('nan'), 'invalid_front', clearance
    if clearance < p['front_stop']:
        return 0., p['max_yaw_rate'], float('nan'), float('nan'), 'corner', clearance
    mask = valid & (a >= math.radians(p['sector_start'])) & (a <= math.radians(p['sector_end']))
    ids = np.flatnonzero(mask)[::p['beam_stride']]
    if len(ids) < p['min_points']:
        return 0., 0., float('nan'), float('nan'), 'lost_wall', clearance
    points = np.column_stack((r[ids]*np.cos(a[ids]), r[ids]*np.sin(a[ids])))
    # Deterministic consensus fit: corners can put two walls in the same sector.
    # Score candidate lines by inlier count, then refit only the winning wall.
    seeds = np.unique(np.linspace(0, len(points)-1, min(10,len(points))).astype(int))
    best = None
    best_count = 0
    for i, first in enumerate(seeds):
        for second in seeds[i+1:]:
            delta = points[second]-points[first]
            length = np.linalg.norm(delta)
            if length < 0.05:
                continue
            normal = np.array([-delta[1], delta[0]])/length
            residual = abs((points-points[first]) @ normal)
            inliers = residual <= p['fit_threshold']
            count = int(inliers.sum())
            if count > best_count:
                best, best_count = inliers, count
    if best is None or best_count < p['min_points']:
        return 0., 0., float('nan'), float('nan'), 'lost_wall', clearance
    points = points[best]
    center = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points-center, full_matrices=False)
    tangent = vt[0]
    if tangent[0] < 0:
        tangent = -tangent
    heading = math.atan2(tangent[1], tangent[0])
    distance = abs(float(tangent[0]*center[1] - tangent[1]*center[0]))
    # Too far from the right wall => negative yaw; positive wall slope => positive yaw.
    yaw = np.clip(-p['kp']*(distance-p['target_distance']) + p['heading_gain']*heading,
                  -p['max_yaw_rate'], p['max_yaw_rate'])
    speed = p['speed'] * max(0.2, 1.0-abs(yaw)/p['max_yaw_rate'])
    return speed, float(yaw), distance, heading, 'tracking', clearance
