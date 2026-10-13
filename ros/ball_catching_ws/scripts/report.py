#!/usr/bin/env python3
"""Build a portable, offline timing dashboard and synchronized motion replay."""
import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

from replay_assets import export_robot_assets
from benchmark_status import launch_failure


def _records(path):
    if not path.exists():
        return []
    with path.open() as source:
        return [json.loads(line) for line in source if line.strip()]


def _stats(values):
    values = sorted(x for x in values if isinstance(x, (float, int)) and math.isfinite(x))
    if not values:
        return None
    return dict(minimum_ms=values[0]*1000, mean_ms=statistics.mean(values)*1000,
                median_ms=statistics.median(values)*1000,
                p95_ms=values[min(len(values)-1, math.ceil(.95*len(values))-1)]*1000,
                p99_ms=values[min(len(values)-1, math.ceil(.99*len(values))-1)]*1000,
                maximum_ms=values[-1]*1000, samples=len(values))


def _cadence(records, field):
    times = sorted(set(r[field] for r in records if field in r))
    gaps = [b-a for a, b in zip(times, times[1:]) if b > a]
    return dict(rate_hz=1/statistics.mean(gaps), gaps=_stats(gaps)) if gaps else None


def _duration_field(records, wall_field):
    if not records:
        return wall_field, 'not recorded'
    if any(r.get('timing_clock') == 'gem5 simulation' for r in records):
        return wall_field.replace('_wall_seconds', '_simulation_seconds'), 'gem5 simulation'
    return wall_field, 'wall'


def _completed_by(record, end):
    return record.get('completed_time', end) <= end


def _processing_stats(records, wall_field):
    field, _ = _duration_field(records, wall_field)
    return _stats([r.get(field) for r in records])


def load_trial(directory):
    directory = Path(directory)
    failure = launch_failure(directory / 'launch_validation.json')
    if failure:
        raise ValueError(failure + ': ' + str(directory))
    result = json.loads((directory / 'result.json').read_text())
    config = json.loads((directory / 'experiment.json').read_text())
    initial = config['initial_pose']
    start_condition = config['start_condition']
    launch = result['launch_time']
    end = result['result_time']
    perception = [r for r in _records(directory / 'perception.jsonl') if launch <= r['capture_time'] <= end and r['receive_time'] <= end and _completed_by(r, end)]
    plans = [r for r in _records(directory / 'interception.jsonl') if launch <= r['time'] <= end and _completed_by(r, end)]
    control_timing = [r for r in _records(directory / 'control_timing.jsonl')
                      if launch <= r['time'] <= end and _completed_by(r, end)]
    with (directory / 'ground_truth.csv').open() as source:
        rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(source)]
    rows = [r for r in rows if r['launched'] and launch <= r['time'] <= end]
    if not rows:
        raise ValueError(f'No launched motion records in {directory}')
    tracked = [r for r in perception if r.get('state') is not None]
    first_track = tracked[0]['capture_time'] if tracked else None
    end_kind = result['flight_end_kind']
    flight_end = result['flight_end_time'] if result['success'] else None
    cup_start = [rows[0]['cup_'+axis] for axis in 'xyz']
    cup_displacement = None
    if flight_end is not None:
        endpoint = min(rows, key=lambda row: abs(row['time']-flight_end))
        cup_displacement = math.sqrt(sum((endpoint['cup_'+axis]-value)**2
                                         for axis, value in zip('xyz', cup_start)))
    # Estimate incoming speed before the first physical impact. Cup contact
    # can slow the ball before it becomes fully contained. Detect a departure
    # from free ballistic velocity above the floor using evaluation data only.
    first_contact = next((i for i, row in enumerate(rows) if row['inside']), None)
    contact_kind = 'before_cup_containment'
    for i, (a, b) in enumerate(zip(rows, rows[1:]), start=1):
        if first_contact is not None and i >= first_contact:
            break
        dt = b['time']-a['time']
        if not 0 < dt <= .012 or min(a['ball_z'], b['ball_z']) <= .05:
            continue
        expected = [a['ball_v'+axis]-(9.81*dt if axis == 'z' else 0.) for axis in 'xyz']
        residual = math.sqrt(sum((b['ball_v'+axis]-v)**2 for axis, v in zip('xyz', expected)))
        if residual > .35:
            first_contact, contact_kind = i, 'before_inferred_impact'
            break
    speed = None
    if first_contact is not None and first_contact > 0:
        speed = math.sqrt(sum(rows[first_contact-1]['ball_v'+axis]**2 for axis in 'xyz'))
    actual_plans = [r for r in plans if r.get('status') == 'planned']
    braking = next((r for r in reversed(actual_plans)
                    if 'braking_start_time' in r and 'braking_end_time' in r), None)
    home_error = max(abs(rows[0][f'fr3_joint{i+1}']-q) for i, q in enumerate(initial)) \
        if start_condition == 'vertical_home' and \
        all(f'fr3_joint{i+1}' in rows[0] for i in range(7)) else None
    control_records = [r for r in control_timing if launch <= r['time'] <= end]
    details = []
    for name, stage, records, source, field, clock, timestamp in [
            ('Perception callback · total', 'perception', perception, 'perception.jsonl',
             'processing_wall_seconds', 'wall', 'capture_time'),
            ('Image preparation', 'perception', perception, 'perception.jsonl',
             'image_conversion_wall_seconds', 'wall', 'capture_time'),
            ('Detection + stereo',
             'perception', perception, 'perception.jsonl', 'detection_wall_seconds', 'wall', 'capture_time'),
            ('State estimation',
             'perception', perception, 'perception.jsonl', 'estimation_wall_seconds', 'wall', 'capture_time'),
            ('State + geometry publication', 'perception', perception, 'perception.jsonl',
             'publication_wall_seconds', 'wall', 'capture_time'),
            ('Interception search', 'planning', plans, 'interception.jsonl',
             'planning_wall_seconds', 'wall', 'time'),
            ('Arm control update', 'control', control_records,
             'control_timing.jsonl',
             'processing_wall_seconds', 'wall', 'time'),
            ('Capture → matched stereo callback', 'transport', perception, 'perception.jsonl',
             'observation_age', 'simulation', 'capture_time'),
            ('Capture → completed perception', 'transport', perception, 'perception.jsonl',
             'completion_age', 'simulation', 'capture_time')]:
        timed = [r for r in records if isinstance(r.get(field), (int, float))]
        if field.endswith('_wall_seconds'):
            field, clock = _duration_field(records, field)
            timed = [r for r in records if isinstance(r.get(field), (int, float))]
        durations = [r[field] for r in timed]
        if field == 'observation_age':
            timed = perception
            durations = [r['receive_time']-r['capture_time'] for r in perception]
        if field == 'completion_age':
            timed = [r for r in perception if isinstance(r.get('completed_time'), (int, float))]
            durations = [r['completed_time']-r['capture_time'] for r in timed]
        cadence = _cadence(timed, timestamp)
        # These are the rates expected of the recorded stream: camera frames
        # for perception, admitted searches for planning (60 ms gate), and
        # the effort controller timer. The planner's outer timer is 30 Hz.
        desired_hz = (config.get('camera_hz', 90.) if stage == 'perception' else
                      min(config.get('planner_hz', 30.), 1. / config.get('plan_interval', .06))
                      if stage == 'planning' and config.get('plan_interval', .06) > 0 else
                      config.get('planner_hz', 30.) if stage == 'planning' else
                      config.get('control_hz', 250.) if stage == 'control' else None)
        details.append(dict(name=name, stage=stage, source=source,
                            field='receive_time - capture_time' if field == 'observation_age' else
                            'completed_time - capture_time' if field == 'completion_age' else field, clock=clock,
                            stats=_stats(durations), cadence=cadence,
                            desired_hz=desired_hz,
                            achieved_hz=cadence['rate_hz'] if cadence else None,
                            rate_percent=(100 * cadence['rate_hz'] / desired_hz
                                          if cadence and desired_hz else None)))
    summary = dict(start_condition=start_condition,
                   evaluation_seconds=end-launch, duration=result['duration'],
                   contained_duration=result['contained_duration'],
                   timing_detail=details,
                   processing_clock=next((r['clock'] for r in details
                                          if r['stage'] in ('perception', 'planning', 'control') and r['stats']),
                                         'not recorded'),
                   expired_perception_count=sum(r.get('status') == 'expired_processing' for r in perception),
                   perception_counts=dict(pairs=len(perception),
                                          detected=sum('observation' in r for r in perception),
                                          tracked=len(tracked))
                       if (directory / 'perception.jsonl').exists() else None,
                   planning_counts=dict(updates=len(plans), planned=len(actual_plans),
                                        infeasible=sum(r.get('status') == 'no_feasible_intercept' for r in plans))
                       if (directory / 'interception.jsonl').exists() else None,
                   home_error_at_launch_degrees=math.degrees(home_error) if home_error is not None else None,
                   braking_start_seconds=braking['braking_start_time']-launch if braking else None,
                   braking_end_seconds=braking['braking_end_time']-launch if braking else None,
                   braking_duration=braking['braking_end_time']-braking['braking_start_time'] if braking else None,
                   planned_cup_speed=math.sqrt(sum(v*v for v in braking['cup_velocity']))
                       if braking and 'cup_velocity' in braking else None,
                   cup_position_at_launch=cup_start, cup_displacement_to_flight_end=cup_displacement,
                   success=result['success'], reason=result['reason'], launch_speed=config['launch_speed'],
                   arrival_speed=speed, arrival_speed_kind=contact_kind if speed is not None else None,
                   flight_seconds=flight_end-launch if flight_end is not None else None,
                   reaction_seconds=flight_end-first_track if flight_end is not None and first_track is not None else None,
                   first_track_seconds=first_track-launch if first_track is not None else None,
                   first_plan_lead_ms=(actual_plans[0]['intercept_time']-actual_plans[0]['time'])*1000 if actual_plans else None,
                   processing={
                       'Perception + estimation': _processing_stats(perception, 'processing_wall_seconds'),
                       'Planning': _processing_stats(plans, 'planning_wall_seconds'),
                       'Arm control': _processing_stats(control_timing, 'processing_wall_seconds')},
                   observation_age=_stats([r['receive_time']-r['capture_time'] for r in perception]),
                   end_kind=end_kind)
    frames = []
    previous = -1e30
    for row in rows:
        if row['time'] - previous < .008 - 1e-8 and row is not rows[-1]:
            continue
        previous = row['time']
        joints = {name: value for name, value in row.items() if name.startswith('fr3_')}
        frames.append(dict(time=row['time']-launch, ball=[row['ball_'+axis] for axis in 'xyz'], joints=joints))
    benchmark_path = directory / 'benchmark.json'
    benchmark = json.loads(benchmark_path.read_text()) if benchmark_path.exists() else None
    return dict(id=directory.name, path=str(directory), config=config, result=result,
                summary=summary, frames=frames, benchmark=benchmark)


def create_dashboard(directories, destination, points=None, trial_ids=None):
    destination = Path(destination)
    models, trials = {}, []
    for directory in directories:
        directory = Path(directory)
        trial = load_trial(directory)
        if trial_ids:
            trial['id'] = trial_ids[str(directory.resolve())]
        robot_path = directory / 'robot.urdf'
        model_id = hashlib.sha256(robot_path.read_bytes()).hexdigest()[:16]
        if model_id not in models:
            models[model_id] = export_robot_assets(robot_path, max_triangles_per_mesh=8000)
        trial['model'] = model_id
        trials.append(trial)
        (directory / 'summary.json').write_text(json.dumps(trial['summary'], indent=2, allow_nan=False) + '\n')
    if points is None:
        points = [dict(id=trial['id'], label=trial['id'], trials=[trial['id']], expected_throws=1,
                       status='complete', config=trial['config']) for trial in trials]
    data = dict(schema_version=2, models=models, trials=trials, points=points)
    dashboard = Path(__file__).resolve().parents[1] / 'dashboard'
    template = (dashboard / 'index.html').read_text()
    viewer = (dashboard / 'replay_viewer.js').read_text()
    app = (dashboard / 'dashboard.js').read_text()
    memory = (dashboard / 'memory_comparison.js').read_text()
    payload = json.dumps(data, separators=(',', ':'), allow_nan=False).replace('</', r'<\/')
    page = template.replace('/* REPLAY_VIEWER */', viewer).replace('/* DASHBOARD_APP */', app)
    page = page.replace('/* MEMORY_COMPARISON */', memory)
    page = page.replace('/* EXPERIMENT_DATA */', payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page)
    print(f'Offline dashboard: {destination.resolve()}')
    return destination


def cpu_comparison_suites(paths):
    """Combine fixed-system suites without colliding case/trial identifiers."""
    directories, points, trial_ids = [], [], {}
    reference = None
    names = set()
    for path in paths:
        path = Path(path).resolve()
        if path.name in names:
            raise ValueError('Comparison suite directory names must be unique')
        names.add(path.name)
        manifest = json.loads((path / 'experiment_manifest.json').read_text())
        if manifest['mode'] != 'gem5':
            raise ValueError('CPU comparisons require gem5 suites')
        configuration = copy.deepcopy(manifest['configuration'])
        cpu_type = configuration['hardware'].pop('cpu_type', 'timing')
        provenance = manifest.get('provenance', {})
        comparable = (configuration, {key: provenance.get(key) for key in (
            'gem5_sha256', 'kernel_sha256', 'execution_sha256', 'image', 'image_size', 'image_mtime_ns')})
        if reference is not None and comparable != reference:
            raise ValueError('CPU comparison settings and artifacts must match except hardware.cpu_type')
        reference = comparable
        for original in manifest['points']:
            point = copy.deepcopy(original)
            point['id'] = path.name + '/' + original['id']
            point['cpu_type'] = cpu_type
            point['label'] = path.name + ' · ' + original['label']
            point['trials'] = [path.name + '/' + name for name in original['trials']]
            points.append(point)
            for name, identifier in zip(original['trials'], point['trials']):
                directory = path / name
                directories.append(directory)
                trial_ids[str(directory)] = identifier
    return directories, points, trial_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.results) > 1 and all((path / 'experiment_manifest.json').exists() for path in args.results):
        try:
            directories, points, trial_ids = cpu_comparison_suites(args.results)
        except ValueError as error:
            parser.error(str(error))
        create_dashboard(directories, args.output, points, trial_ids)
        return
    directories = []
    points = None
    for path in args.results:
        if (path / 'experiment_manifest.json').exists():
            manifest = json.loads((path / 'experiment_manifest.json').read_text())
            if points is not None:
                parser.error('Use one experiment manifest at a time')
            points = manifest['points']
            directories.extend(path / name for p in points for name in p['trials'])
        elif (path / 'result.json').exists():
            directories.append(path)
        else:
            directories.extend(sorted(p.parent for p in path.rglob('result.json')))
    if not directories and points is None:
        parser.error('No completed trial results found')
    create_dashboard(directories, args.output, points)


if __name__ == '__main__':
    main()
