"""Run-level benchmark metrics shared by static and interactive comparisons."""
import numpy as np

def summarize(rows, warmup=5., observation=None):
    def col(key):
        return np.asarray([float(r.get(key, 'nan')) for r in rows])
    t = col('elapsed')
    dt = np.diff(t, append=t[-1])
    keep = t >= warmup
    error = col('gt_error')
    valid = keep & np.isfinite(error) & (dt > 0)
    total = float(dt[keep].sum())
    covered = float(dt[valid].sum())
    weighted = lambda values: float(np.sum(values[valid]*dt[valid])/covered) if covered else None
    timing = col('compute_ms')[keep]
    timing = timing[np.isfinite(timing)]
    intervals = col('dt_sim')[keep]
    intervals = intervals[np.isfinite(intervals)]
    wall_intervals = col('dt_wall')[keep]
    wall_intervals = wall_intervals[np.isfinite(wall_intervals)]
    wall = col('wall_elapsed')
    states = np.asarray([r.get('state', 'unobserved') for r in rows])
    def percentile(key, scale=1.):
        values = col(key)[keep]
        values = values[np.isfinite(values)]
        return float(np.percentile(values, 95)*scale) if len(values) else None
    observed = observation == 'host_command_receipt'
    rate = float(1/np.mean(intervals)) if len(intervals) and np.mean(intervals)>0 else None
    interval_p95 = float(np.percentile(wall_intervals,95)*1000) if len(wall_intervals) else None
    return dict(rmse_m=np.sqrt(weighted(error**2)) if covered else None,
                mae_m=weighted(abs(error)), max_abs_error_m=float(np.max(abs(error[valid]))) if covered else None,
                gt_coverage=covered/total if total else 0.,
                stopped_fraction=float(dt[keep & (col('linear_cmd') == 0)].sum()/total) if total else None,
                stale_fraction=float(dt[keep & (states == 'stale_scan')].sum()/total) if total and not observed else None,
                command_rate_observed_hz_sim=rate if observed else None,
                command_interval_p95_host_ms=interval_p95 if observed else None,
                path_m=float(col('path_m')[-1]-col('path_m')[np.flatnonzero(keep)[0]]) if keep.any() else 0.,
                compute_p95_ms=float(np.percentile(timing,95)) if len(timing) else None,
                compute_max_ms=float(np.max(timing)) if len(timing) else None,
                actual_control_hz_sim=rate if not observed else None,
                timer_interval_p95_wall_ms=interval_p95 if not observed else None,
                scan_age_p95_s=percentile('scan_age'),
                host_scan_age_p95_s=percentile('host_scan_age'),
                real_time_factor=float((t[-1]-t[0])/(wall[-1]-wall[0])) if wall[-1]>wall[0] else None,
                sim_duration_s=float(t[-1]), warmup_s=warmup)


def run_label(run):
    if run.name.startswith('attempt_') and run.parent.parent.name == 'runs':
        return f'{run.parents[2].name}/{run.parent.name}'
    return run.name


def derive_metrics(rows, poses, arena):
    """Derive arena-specific metrics from raw samples and odometry."""
    import math
    from .world import wall_distance
    index, path, previous = 0, 0., None
    for row in rows:
        now = float(row['sim_time'])
        while index < len(poses) and float(poses[index]['sim_time']) <= now:
            pose = poses[index]
            xy = float(pose['x']), float(pose['y'])
            if previous is not None:
                path += math.hypot(xy[0]-previous[0], xy[1]-previous[1])
            previous = xy
            index += 1
        age = now-float(row['pose_stamp'])
        distance = wall_distance(float(row['x']), float(row['y']), **arena) if 0 <= age <= .2 else float('nan')
        row.update(gt_age=age, gt_distance=distance,
                   gt_error=distance-float(row['target_distance']), path_m=path)
