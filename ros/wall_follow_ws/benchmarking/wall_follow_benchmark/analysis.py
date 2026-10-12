"""Run-level benchmark metrics for the dashboard and summary exports."""
import numpy as np

def summarize(rows, warmup=0.):
    def col(key):
        return np.asarray([float(r.get(key, 'nan')) for r in rows])
    t = col('elapsed')
    # Weight the portion of each command interval remaining after warmup.
    dt = np.maximum(0., np.diff(t, append=t[-1]) - np.maximum(0., warmup-t))
    keep = dt > 0
    error = col('gt_error')
    valid = keep & np.isfinite(error) & (dt > 0)
    total = float(dt[keep].sum())
    covered = float(dt[valid].sum())
    weighted = lambda values: float(np.sum(values[valid]*dt[valid])/covered) if covered else None
    wall = col('wall_elapsed')
    gaps = np.diff(t)
    gap_keep = (t[1:] > warmup) & (t[:-1] >= warmup) & (gaps > 0)
    gap_values = gaps[gap_keep]
    hz = col('control_hz')[1:][gap_keep]
    valid_hz = np.isfinite(hz) & (hz > 0)
    late = gap_values[valid_hz] > 1.5 / hz[valid_hz]
    scan_age = col('host_scan_age')
    scan_valid = (t >= warmup) & np.isfinite(scan_age) & (scan_age >= 0)

    first_command = col('sim_time')[0]
    return dict(first_command_sim_s=float(first_command) if np.isfinite(first_command) else None,
                command_gap_p95_ms=float(np.percentile(gap_values, 95) * 1000) if len(gap_values) else None,
                command_gap_max_ms=float(gap_values.max() * 1000) if len(gap_values) else None,
                command_late_fraction=float(late.mean()) if len(late) else None,
                host_scan_age_p95_ms=float(np.percentile(scan_age[scan_valid], 95) * 1000) if scan_valid.any() else None,
                rmse_m=np.sqrt(weighted(error**2)) if covered else None,
                mae_m=weighted(abs(error)), max_abs_error_m=float(np.max(abs(error[valid]))) if covered else None,
                gt_coverage=covered/total if total else None,
                path_m=float(col('path_m')[-1]-np.interp(max(warmup,t[0]),t,col('path_m'))) if total else None,
                real_time_factor=float((t[-1]-t[0])/(wall[-1]-wall[0])) if wall[-1]>wall[0] else None)


def run_label(run):
    if run.name.startswith('attempt_') and run.parent.parent.name == 'runs':
        return f'{run.parents[2].name}/{run.parent.name}'
    return run.name


def derive_metrics(rows, poses, arena):
    """Derive arena-specific metrics from raw samples and odometry."""
    import math
    from wall_follow_sim.world import wall_distance
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
