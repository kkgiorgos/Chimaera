"""Aggregate completed Chimaera intervals; startup is reported separately."""
import csv
import math

from .metrics import SIMULATION

METRICS = {key.removeprefix('timing_'): label for key, label in SIMULATION.items()
           if key.startswith('timing_')}
SCOPE = ('Host wall times over completed co-simulation intervals, including pre-collection '
         'steps. Startup is separate; benchmark warmup does not crop simulation timings. '
         'Phase times include communication and completion waits.')


def summarize(path):
    with path.open(newline='') as stream:
        rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(stream)]
    if not rows:
        raise ValueError('No completed co-simulation timing intervals')
    if any(not math.isfinite(v) or v < 0 for row in rows for v in row.values()):
        raise ValueError('Invalid co-simulation timing values')
    keys = ('sim_seconds', 'gem5_sim_seconds', 'gem5_wall_seconds',
            'gazebo_wall_seconds', 'other_wall_seconds')
    total = {key: sum(row[key] for row in rows) for key in keys}

    def rate(sim, wall):
        return sim / wall if wall > 0 else None

    return dict(
        cosim_realtime_factor=rate(total['sim_seconds'], rows[-1]['elapsed_wall_seconds']),
        gem5_phase_realtime_factor=rate(total['gem5_sim_seconds'], total['gem5_wall_seconds']),
        gazebo_phase_realtime_factor=rate(total['sim_seconds'], total['gazebo_wall_seconds']),
        gem5_wall_seconds=total['gem5_wall_seconds'],
        gazebo_wall_seconds=total['gazebo_wall_seconds'],
        other_wall_seconds=total['other_wall_seconds'],
        elapsed_wall_seconds=rows[-1]['elapsed_wall_seconds'],
        startup_wall_seconds=rows[0]['startup_wall_seconds'])


def load_metrics(directory):
    path = directory/'timing.csv'
    result = summarize(path) if path.exists() else {}
    return {f'timing_{key}': result.get(key) for key in METRICS}
