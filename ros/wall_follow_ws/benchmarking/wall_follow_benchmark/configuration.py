"""Standalone benchmark configuration defaults and validation."""
import math

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
