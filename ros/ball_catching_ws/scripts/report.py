#!/usr/bin/env python3
"""Build a portable, offline timing dashboard and synchronized motion replay."""
import argparse
import bisect
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

from replay_assets import export_robot_assets


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


def load_trial(directory):
    directory = Path(directory)
    result = json.loads((directory / 'result.json').read_text())
    config = json.loads((directory / 'experiment.json').read_text())
    upright = [0., -math.pi/4, 0., -3*math.pi/4, 0., math.pi/2, math.pi/4]
    vertical = [0., 0., 0., -.14, 0., .50, math.pi/4]
    initial = config.get('initial_pose', [])
    start_condition = ('vertical_home' if len(initial) == 7 and
                       all(abs(q-home) < 1e-6 for q, home in zip(initial, vertical))
                       else 'upright_home' if len(initial) == 7 and
                       all(abs(q-home) < 1e-6 for q, home in zip(initial, upright))
                       else 'historical_prepared_pose' if initial else 'unrecorded')
    launch = result['launch_time']
    bounces = _records(directory / 'bounces.jsonl')
    last_bounce = bounces[-1]['time'] if bounces else None
    perception = [r for r in _records(directory / 'perception.jsonl') if r['capture_time'] >= launch]
    plans = [r for r in _records(directory / 'interception.jsonl') if r['time'] >= launch]
    controls = _records(directory / 'control.jsonl')
    control_timing = _records(directory / 'control_timing.jsonl')
    grips = [r for r in _records(directory / 'gripper.jsonl') if r['time'] >= launch]
    with (directory / 'ground_truth.csv').open() as source:
        rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(source)]
    rows = [r for r in rows if r['launched'] and r['time'] >= launch]
    if not rows:
        raise ValueError(f'No launched motion records in {directory}')
    presentation_success = None
    if config.get('present') and (directory/'presentation_done.json').exists():
        presentation = json.loads((directory/'presentation_done.json').read_text())
        end_sample = min(rows, key=lambda r: abs(r['time']-presentation['time']))
        presentation_success = presentation.get('held', bool(end_sample['inside']))
    tracked = [r for r in perception if r.get('state') is not None]
    first_track = tracked[0]['capture_time'] if tracked else None
    target = config.get('target')
    arrival = None
    if target:
        # Target-plane crossing is passive evaluation, independent of whether
        # the planner caught the ball or chose a different interception point.
        offset = [x-y for x, y in zip(target, config['launch_position'])]
        axis_index = max(range(3), key=lambda i: abs(offset[i]))
        coordinate = 'ball_'+'xyz'[axis_index]
        sign = math.copysign(1., offset[axis_index])
        for a, b in zip(rows, rows[1:]):
            if abs(b[coordinate]-a[coordinate]) > 1e-12 and sign*(a[coordinate] - target[axis_index]) <= 0 <= sign*(b[coordinate] - target[axis_index]):
                fraction = (target[axis_index] - a[coordinate])/(b[coordinate] - a[coordinate])
                arrival = {k: a[k] + fraction*(b[k]-a[k]) for k in a}
                # Use the preceding velocity sample. Interpolating across a
                # collision would understate incoming speed by mixing impact
                # velocities with free-flight velocities.
                for axis in 'xyz':
                    if 'ball_v' + axis in a:
                        arrival['ball_v' + axis] = a['ball_v' + axis]
                break
    end_kind = result.get('flight_end_kind', 'stable_capture' if result['success'] else 'unknown')
    flight_end = result.get('flight_end_time', result['result_time'] - result['retention'] if result['success'] else None)
    if end_kind in ('timeout', 'unknown'):
        flight_end = None
    # Show how much reaching the benchmark actually requires. Use the recorded
    # launch pose and flight endpoint, excluding optional presentation motion.
    hand_start = hand_displacement = None
    if all('cup_'+axis in rows[0] for axis in 'xyz'):
        hand_start = [rows[0]['cup_'+axis] for axis in 'xyz']
        if flight_end is not None:
            endpoint = min(rows, key=lambda row: abs(row['time']-flight_end))
            hand_displacement = math.sqrt(sum((endpoint['cup_'+axis]-value)**2
                                              for axis, value in zip('xyz', hand_start)))
    interception = result['result_time'] - result['retention'] if result['success'] else arrival['time'] if arrival else None
    first_contact = next((i for i, r in enumerate(rows) if
                          (last_bounce is None or r['time'] > last_bounce) and
                          (r.get('left_contact') or r.get('right_contact'))), None)
    contact_kind = 'before_finger_contact'
    # Palm contact can arrest the ball before either rubber tip touches it.
    # Older records lack palm-contact flags: infer a substantial velocity jump
    # away from the floor, using host flight physics for evaluation only. Never
    # report a nearly stationary post-impact ball as its incoming arrival speed.
    drag = .5*config.get('air_density', 1.225)*config.get('drag_coefficient', 0.)*math.pi*.0335**2/.057
    for i, (a, b) in enumerate(zip(rows, rows[1:]), start=1):
        if first_contact is not None and i >= first_contact:
            break
        if last_bounce is not None and a['time'] <= last_bounce+.012:
            continue
        dt = b['time']-a['time']
        if not 0 < dt <= .012 or min(a['ball_z'], b['ball_z']) <= .05 or 'ball_vx' not in a:
            continue
        velocity = [a['ball_v'+axis] for axis in 'xyz']
        norm = math.sqrt(sum(v*v for v in velocity))
        expected = [v-dt*drag*norm*v-(9.81*dt if axis == 'z' else 0.) for v, axis in zip(velocity, 'xyz')]
        residual = math.sqrt(sum((b['ball_v'+axis]-v)**2 for axis, v in zip('xyz', expected)))
        if residual > .35:
            first_contact, contact_kind = i, 'before_inferred_impact'
            break
    arrival_record = rows[max(0, first_contact-1)] if first_contact is not None else arrival
    speed = None
    if arrival_record and 'ball_vx' in arrival_record:
        speed = math.sqrt(sum(arrival_record['ball_v' + axis]**2 for axis in 'xyz'))
    actual_plans = [r for r in plans if r.get('status') == 'planned']
    braking = next((r for r in reversed(actual_plans)
                    if 'braking_start_time' in r and 'braking_end_time' in r), None)
    home_error = max(abs(rows[0][f'fr3_joint{i+1}']-q) for i, q in enumerate(initial)) \
        if start_condition in ('vertical_home', 'upright_home') and \
        all(f'fr3_joint{i+1}' in rows[0] for i in range(7)) else None
    control_records = [r for r in (control_timing or controls) if r['time'] >= launch]
    detailed_perception = any('image_conversion_wall_seconds' in r for r in perception)
    details = []
    for name, stage, records, source, field, clock, timestamp in [
            ('Perception callback · total', 'perception', perception, 'perception.jsonl',
             'processing_wall_seconds', 'wall', 'capture_time'),
            ('Image preparation', 'perception', perception, 'perception.jsonl',
             'image_conversion_wall_seconds', 'wall', 'capture_time'),
            ('Detection + stereo' if detailed_perception else 'Image preparation + detection (legacy)',
             'perception', perception, 'perception.jsonl', 'detection_wall_seconds', 'wall', 'capture_time'),
            ('State estimation' if detailed_perception else 'Estimation + geometry publish (legacy)',
             'perception', perception, 'perception.jsonl', 'estimation_wall_seconds', 'wall', 'capture_time'),
            ('State + geometry publication', 'perception', perception, 'perception.jsonl',
             'publication_wall_seconds', 'wall', 'capture_time'),
            ('Interception search', 'planning', plans, 'interception.jsonl',
             'planning_wall_seconds', 'wall', 'time'),
            ('Arm control update', 'control', control_records,
             'control_timing.jsonl' if control_timing else 'control.jsonl',
             'processing_wall_seconds', 'wall', 'time'),
            ('Capture → matched stereo callback', 'transport', perception, 'perception.jsonl',
             'observation_age', 'simulation', 'capture_time')]:
        timed = [r for r in records if isinstance(r.get(field), (int, float))]
        durations = [r[field] for r in timed]
        if field == 'observation_age':
            timed = perception
            durations = [r['receive_time']-r['capture_time'] for r in perception]
        cadence = _cadence(timed, timestamp)
        # These are the rates expected of the recorded stream: camera frames
        # for perception, admitted searches for planning (60 ms gate), and
        # the effort controller timer. The planner's outer timer is 30 Hz.
        desired_hz = (config.get('camera_hz', 90.) if stage == 'perception' else
                      1. / .06 if stage == 'planning' else
                      250. if stage == 'control' and config.get('mode') == 'cup' else None)
        details.append(dict(name=name, stage=stage, source=source,
                            field='receive_time - capture_time' if field == 'observation_age' else field, clock=clock,
                            stats=_stats(durations), cadence=cadence,
                            desired_hz=desired_hz,
                            achieved_hz=cadence['rate_hz'] if cadence else None,
                            rate_percent=(100 * cadence['rate_hz'] / desired_hz
                                          if cadence and desired_hz else None)))
    if config.get('mode') != 'cup':
        details.append(dict(name='Gripper control update', stage='control', source='gripper.jsonl',
                            field='processing_wall_seconds', clock='wall',
                            stats=_stats([r.get('processing_wall_seconds') for r in grips]),
                            cadence=_cadence(grips, 'time')))
    post_track = None
    if last_bounce is not None:
        reset = max([last_bounce, *[r['capture_time'] for r in perception
                    if r.get('track_reset') and last_bounce < r['capture_time'] < (interception or math.inf)]])
        post_track = next((r['capture_time'] for r in tracked if r['capture_time'] > reset), None)
    approaches = [r for r in plans if r.get('status') == 'visual_approach']
    summary = dict(start_condition=start_condition,
                   timing_detail=details,
                   perception_counts=dict(pairs=len(perception),
                                          detected=sum('observation' in r for r in perception),
                                          tracked=len(tracked)),
                   planning_counts=dict(updates=len(plans), planned=len(actual_plans),
                                        infeasible=sum(r.get('status') == 'no_feasible_intercept' for r in plans)),
                   home_error_at_launch_degrees=math.degrees(home_error) if home_error is not None else None,
                   braking_start_seconds=braking['braking_start_time']-launch if braking else None,
                   braking_end_seconds=braking['braking_end_time']-launch if braking else None,
                   braking_duration=braking['braking_end_time']-braking['braking_start_time'] if braking else None,
                   planned_cup_speed=math.sqrt(sum(v*v for v in braking['cup_velocity']))
                       if braking and 'cup_velocity' in braking else None,
                   visual_approach_seconds=approaches[0]['time']-launch if approaches else None,
                   visual_approach_duration=approaches[0]['duration'] if approaches else None,
                   hand_position_at_launch=hand_start, hand_displacement_to_flight_end=hand_displacement,
                   presentation_success=presentation_success, success=result['success'], reason=result['reason'], launch_speed=config['launch_speed'],
                   bounce_count=result.get('bounce_count', len(bounces)),
                   last_bounce_seconds=last_bounce-launch if last_bounce is not None else None,
                   post_bounce_track_seconds=post_track-last_bounce if post_track is not None else None,
                   post_bounce_reaction_seconds=interception-post_track if post_track is not None and interception is not None else None,
                   arrival_speed=speed, arrival_speed_kind=contact_kind if first_contact is not None else 'before_target_plane',
                   flight_seconds=flight_end-launch if flight_end is not None else None,
                   reaction_seconds=interception-first_track if interception is not None and first_track is not None else None,
                   first_track_seconds=first_track-launch if first_track is not None else None,
                   first_plan_lead_ms=(actual_plans[0]['intercept_time']-actual_plans[0]['time'])*1000 if actual_plans else None,
                   processing={
                       'Perception + estimation': _stats([r.get('processing_wall_seconds') for r in perception]),
                       'Planning': _stats([r.get('planning_wall_seconds') for r in plans]),
                       'Arm control': _stats([r.get('processing_wall_seconds') for r in (control_timing or controls) if r['time'] >= launch]),
                       'Gripper control': _stats([r.get('processing_wall_seconds') for r in grips])},
                   observation_age=_stats([r['receive_time']-r['capture_time'] for r in perception]),
                   end_kind=end_kind, arm_timing_sampled=not bool(control_timing))
    control_times = [r['time'] for r in controls]
    frames = []
    previous = -1e30
    for row in rows:
        if row['time'] - previous < .008 - 1e-8 and row is not rows[-1]:
            continue
        previous = row['time']
        joints = {name: value for name, value in row.items() if name.startswith('fr3_')}
        if not joints and controls:
            j = min(max(bisect.bisect_right(control_times, row['time'])-1, 0), len(controls)-1)
            joints = {f'fr3_joint{i+1}': q for i, q in enumerate(controls[j]['joints'])}
        frames.append(dict(time=row['time']-launch, ball=[row['ball_'+axis] for axis in 'xyz'], joints=joints))
    return dict(id=directory.name, path=str(directory), config=config, result=result,
                summary=summary, frames=frames, bounces=[{**b, 'time': b['time']-launch} for b in bounces])


def create_dashboard(directories, destination, points=None):
    destination = Path(destination)
    models, trials = {}, []
    for directory in directories:
        directory = Path(directory)
        trial = load_trial(directory)
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
    data = dict(schema_version=1, models=models, trials=trials, points=points)
    dashboard = Path(__file__).resolve().parents[1] / 'dashboard'
    template = (dashboard / 'index.html').read_text()
    viewer = (dashboard / 'replay_viewer.js').read_text()
    app = (dashboard / 'dashboard.js').read_text()
    payload = json.dumps(data, separators=(',', ':'), allow_nan=False).replace('</', r'<\/')
    page = template.replace('/* REPLAY_VIEWER */', viewer).replace('/* DASHBOARD_APP */', app)
    page = page.replace('/* EXPERIMENT_DATA */', payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page)
    print(f'Offline dashboard: {destination.resolve()}')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
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
