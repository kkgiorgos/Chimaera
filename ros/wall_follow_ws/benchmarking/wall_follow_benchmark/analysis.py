"""Run-level benchmark metrics shared by static and interactive comparisons."""
import numpy as np

def summarize(rows, warmup=5.):
    def col(key):
        return np.asarray([float(r[key]) for r in rows])
    t = col('elapsed')
    dt = np.diff(t, append=t[-1])
    keep = t >= warmup
    error = col('gt_error')
    valid = keep & np.isfinite(error) & (dt > 0)
    total = float(dt[keep].sum())
    covered = float(dt[valid].sum())
    weighted = lambda values: float(np.sum(values[valid]*dt[valid])/covered) if covered else None
    timing = col('compute_ms')[keep]
    intervals = col('dt_sim')[keep]
    intervals = intervals[np.isfinite(intervals)]
    wall_intervals = col('dt_wall')[keep]
    wall_intervals = wall_intervals[np.isfinite(wall_intervals)]
    wall = col('wall_elapsed')
    cpu = col('cpu_seconds')
    states = np.asarray([r['state'] for r in rows])
    return dict(rmse_m=np.sqrt(weighted(error**2)) if covered else None,
                mae_m=weighted(abs(error)), max_abs_error_m=float(np.max(abs(error[valid]))) if covered else None,
                gt_coverage=covered/total if total else 0.,
                stopped_fraction=float(dt[keep & (col('linear_cmd') == 0)].sum()/total) if total else None,
                stale_fraction=float(dt[keep & (states == 'stale_scan')].sum()/total) if total else None,
                path_m=float(col('path_m')[-1]-col('path_m')[np.flatnonzero(keep)[0]]) if keep.any() else 0.,
                compute_p95_ms=float(np.percentile(timing,95)) if len(timing) else None,
                compute_max_ms=float(np.max(timing)) if len(timing) else None,
                actual_control_hz_sim=float(1/np.mean(intervals)) if len(intervals) and np.mean(intervals)>0 else None,
                timer_interval_p95_wall_ms=float(np.percentile(wall_intervals,95)*1000) if len(wall_intervals) else None,
                scan_age_p95_s=float(np.nanpercentile(col('scan_age')[keep],95)) if np.isfinite(col('scan_age')[keep]).any() else None,
                real_time_factor=float((t[-1]-t[0])/(wall[-1]-wall[0])) if wall[-1]>wall[0] else None,
                controller_cpu_core_fraction=float((cpu[-1]-cpu[0])/(wall[-1]-wall[0])) if wall[-1]>wall[0] else None,
                rss_peak_mib=float(np.max(col('rss_kib'))/1024),
                sim_duration_s=float(t[-1]), warmup_s=warmup)


def run_label(run):
    if run.name.startswith('attempt_') and run.parent.parent.name == 'runs':
        return f'{run.parents[2].name}/{run.parent.name}'
    return run.name


def derive_metrics(rows, poses, arena):
    """Derive arena-specific metrics from schema-v2 raw samples and odometry."""
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
