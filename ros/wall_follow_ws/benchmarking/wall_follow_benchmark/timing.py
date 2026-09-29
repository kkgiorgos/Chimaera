"""Aggregate host-observed, completed co-simulation intervals (boot excluded)."""
import csv
import math


def summarize(path):
    with path.open(newline='') as stream:
        rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(stream)]
    if not rows:
        raise ValueError('No completed co-simulation timing intervals')
    if any(not math.isfinite(v) or v < 0 for row in rows for v in row.values()):
        raise ValueError('Invalid co-simulation timing values')
    keys = ('sim_seconds', 'gem5_sim_seconds', 'gem5_wall_seconds',
            'gazebo_wall_seconds', 'other_wall_seconds', 'pacing_wall_seconds', 'wall_seconds')
    total = {key: sum(row[key] for row in rows) for key in keys}
    def rate(sim, wall):
        return sim / wall if wall > 0 else None
    total.update(schema_version=1, completed_steps=len(rows),
                 startup_wall_seconds=rows[0]['startup_wall_seconds'],
                 elapsed_wall_seconds=rows[-1]['elapsed_wall_seconds'],
                 cosim_realtime_factor=rate(total['sim_seconds'], rows[-1]['elapsed_wall_seconds']),
                 unpaced_realtime_factor=rate(total['sim_seconds'], total['wall_seconds'] - total['pacing_wall_seconds']),
                 gem5_phase_realtime_factor=rate(total['gem5_sim_seconds'], total['gem5_wall_seconds']),
                 gazebo_phase_realtime_factor=rate(total['sim_seconds'], total['gazebo_wall_seconds']),
                 gem5_wall_seconds_per_sim_second=rate(total['gem5_wall_seconds'], total['gem5_sim_seconds']),
                 gazebo_wall_seconds_per_sim_second=rate(total['gazebo_wall_seconds'], total['sim_seconds']),
                 cosim_wall_seconds_per_sim_second=rate(rows[-1]['elapsed_wall_seconds'], total['sim_seconds']),
                 scope='completed host timing intervals; startup and incomplete intervals excluded')
    return total


# Shared labels for exported comparison metrics, static plots, and the dashboard.
METRICS = {
    'cosim_realtime_factor': 'Overall co-simulation rate (sim s / wall s)',
    'unpaced_realtime_factor': 'Unpaced co-simulation rate (sim s / wall s)',
    'gem5_phase_realtime_factor': 'gem5 phase rate (sim s / phase wall s)',
    'gazebo_phase_realtime_factor': 'Gazebo phase rate (sim s / phase wall s)',
    'cosim_wall_seconds_per_sim_second': 'Overall slowdown (wall s / sim s)',
    'gem5_wall_seconds_per_sim_second': 'gem5 phase slowdown (wall s / sim s)',
    'gazebo_wall_seconds_per_sim_second': 'Gazebo phase slowdown (wall s / sim s)',
    'gem5_wall_seconds': 'gem5 phase wall time (s)',
    'gazebo_wall_seconds': 'Gazebo phase wall time (s)',
    'other_wall_seconds': 'Settling / other step wall time (s)',
    'pacing_wall_seconds': 'Pacing phase wall time (s)',
    'elapsed_wall_seconds': 'Overall elapsed wall time, excluding startup (s)',
    'startup_wall_seconds': 'Startup wall time (s)',
    'sim_seconds': 'Recorded Gazebo progress (sim s)',
    'gem5_sim_seconds': 'Recorded gem5 progress (sim s)',
    'completed_steps': 'Completed timing intervals',
}
SCOPE = ('Host-observed completed co-simulation intervals, including pre-collection warmup. '
         'Benchmark warmup and signal time filters do not apply. Startup is separate. '
         'Simulator phases include transport and completion-observation latency. '
         'Repetitions have equal weight; missing timing data is not zero.')


def load_metrics(directory):
    """Prefer raw intervals; support copied summary-only runs and older summaries."""
    import json
    raw, summary = directory/'timing.csv', directory/'timing_summary.json'
    if raw.exists():
        result = summarize(raw)
    elif summary.exists():
        result = json.loads(summary.read_text())
        if result.get('schema_version') != 1:
            raise ValueError('Unsupported timing summary schema')
    else:
        return {f'timing_{key}': None for key in METRICS}
    for key in METRICS:
        value = result.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError(f'Invalid timing value: {key}')
    for name, wall, sim in (
        ('cosim', 'elapsed_wall_seconds', 'sim_seconds'),
        ('gem5', 'gem5_wall_seconds', 'gem5_sim_seconds'),
        ('gazebo', 'gazebo_wall_seconds', 'sim_seconds'),
    ):
        key = f'{name}_wall_seconds_per_sim_second'
        if result.get(key) is None and result.get(sim, 0) and result.get(wall) is not None:
            result[key] = result[wall] / result[sim]
    return {f'timing_{key}': result.get(key) for key in METRICS}
