#!/usr/bin/env python3
"""Evaluate explicit speed/distance points with five seeded throws per point."""
import argparse
import json
import math
from pathlib import Path
import random
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/ball_catching_sim'))
from ball_catching_sim.flight import aimed_throw, bounced_throw, drag_factor, court_net_height, COURT_LENGTH
from report import create_dashboard


def prepare_point(speed, position, target, jitter, seed, drag_coefficient, court=True, arc='low', bounces=0, restitution=.745, tangent_ratio=.6):
    """Same seeded target perturbations at every point; no performance filtering."""
    if len(target) != 3 or len(jitter) != 3 or not all(math.isfinite(x) for x in (*target, *jitter)):
        raise ValueError('target and jitter must contain three finite values')
    if any(x < 0 for x in jitter):
        raise ValueError('target jitter must be nonnegative')
    cases = []
    for i in range(5):
        randomizer = random.Random(seed + i)
        sampled = [x + randomizer.uniform(-spread, spread) for x, spread in zip(target, jitter)]
        net_height = court_net_height(position, sampled) if court else .914
        if bounces and not court:
            raise ValueError('bounces require a court scene')
        bounce_options = dict(bounces=bounces, restitution=restitution, tangent_ratio=tangent_ratio) if bounces else {}
        flight = (bounced_throw if bounces else aimed_throw)(position, sampled, speed, drag=drag_factor(drag_coefficient),
                             net_x=COURT_LENGTH/2 if court else None, net_height=net_height, arc=arc, **bounce_options)
        cases.append(dict(seed=seed+i, target=sampled, direction=list(flight.direction),
                          predicted_flight_time=flight.flight_time,
                          predicted_arrival_speed=flight.arrival_speed, predicted_bounces=flight.bounce_events))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=['gentle', 'court', 'court-bounce'], default='gentle')
    parser.add_argument('--speeds', type=float, nargs='+', help='Launch speed in m/s')
    parser.add_argument('--distances', type=float, nargs='+', help='Launcher x position from robot baseline, metres')
    parser.add_argument('--launch-height', type=float)
    parser.add_argument('--arc', choices=['low', 'high'])
    parser.add_argument('--gripper-lead', type=float)
    parser.add_argument('--target-pitch', '--home-pitch', dest='home_pitch', type=float,
                        help='Target reachability-check axis elevation; initial home is always upright')
    parser.add_argument('--bounces', type=int, choices=range(5))
    parser.add_argument('--court-restitution', type=float, default=.745)
    parser.add_argument('--court-tangent-ratio', type=float, default=.6)
    parser.add_argument('--target', type=float, nargs=3, default=[.5, 0., .8])
    parser.add_argument('--target-jitter', type=float, nargs=3, default=[.02, .02, .01])
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--drag-coefficient', type=float, default=.55)
    parser.add_argument('--camera-position', type=float, nargs=3)
    parser.add_argument('--camera-hz', type=float, default=90.)
    parser.add_argument('--resolution', type=int, nargs=2)
    parser.add_argument('--wall-timeout', type=float, default=180.)
    parser.add_argument('--prepare-only', action='store_true', help='Validate and record points without running simulations')
    args = parser.parse_args()
    court = args.profile in ('court', 'court-bounce')
    if args.gripper_lead is None:
        args.gripper_lead = .025 if args.profile == 'court-bounce' else 0.
    if args.resolution is None:
        args.resolution = [1280, 960] if args.profile == 'court-bounce' else [640, 480]
    if args.camera_position is None:
        args.camera_position = [-1., .6, 1.2] if args.profile == 'court-bounce' else [-.65, 0., 1.]
    if args.home_pitch is None:
        args.home_pitch = 30. if args.profile == 'court-bounce' else 0. if court else 90.
    if args.bounces is None:
        args.bounces = 2 if args.profile == 'court-bounce' else 0
    if args.bounces and not court:
        parser.error('bounces require a court profile')
    if args.arc is None:
        args.arc = 'low' if court else 'high'
    if args.speeds is None:
        args.speeds = [14.] if args.profile == 'court-bounce' else [20., 30., 40., 50., 67.056] if court else [1.5, 2.]
    if args.distances is None:
        args.distances = [COURT_LENGTH] if court else [.5]
    if args.launch_height is None:
        args.launch_height = 2.5 if court else 1.1
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output directory must be empty or new')
    if not all(math.isfinite(x) and x > 0 for x in (*args.speeds, *args.distances, args.launch_height)):
        parser.error('speeds, distances and launch height must be finite and positive')
    output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name('run_trial.py')
    validator = script.parents[1] / 'install/ball_catching_robot/lib/ball_catching_robot/validate_model'
    # Use a hand-enabled URDF already prepared by the scene generator. Creating
    # this model is configuration work, not robot estimation/planning at runtime.
    from ball_catching_sim.scene import robot_urdf
    model_path = output / 'validation_robot.urdf'
    model_path.write_text(robot_urdf(mode='gripper'))
    manifest = dict(schema_version=1, profile=args.profile, seed=args.seed, throws_per_point=5,
                    acceptance='5/5 physical catches', points=[])
    def save():
        temporary = output / 'experiment_manifest.json.tmp'
        temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False)+'\n')
        temporary.replace(output / 'experiment_manifest.json')
    completed = []
    infrastructure_error = False
    for distance in args.distances:
        for speed in args.speeds:
            identifier = f'point-{len(manifest["points"]):03d}'
            position = [distance, 0., args.launch_height]
            point = dict(id=identifier, label=f'{speed:g} m/s · {distance:g} m', expected_throws=5,
                         config=dict(launch_speed=speed, launch_position=position, target=args.target,
                                     target_jitter=args.target_jitter, drag_coefficient=args.drag_coefficient,
                                     start_condition='upright_home', court=court, profile=args.profile,
                                     arc=args.arc, allowed_bounces=args.bounces,
                                     court_restitution=args.court_restitution, court_tangent_ratio=args.court_tangent_ratio, target_pitch=args.home_pitch, camera_position=args.camera_position, gripper_motor_lead=args.gripper_lead),
                         trials=[], status='prepared', cases=[])
            manifest['points'].append(point)
            try:
                point['cases'] = prepare_point(speed, position, args.target, args.target_jitter, args.seed, args.drag_coefficient, court=court, arc=args.arc, bounces=args.bounces,
                                                     restitution=args.court_restitution, tangent_ratio=args.court_tangent_ratio)
                for case in point['cases']:
                    check = subprocess.run([str(validator), str(model_path),
                                            '--pitched-target', str(args.home_pitch), *map(str, case['target'])],
                                           capture_output=True, text=True)
                    if check.returncode:
                        raise ValueError(check.stderr.strip())
            except ValueError as error:
                point.update(status='invalid', reason=str(error)); save()
                print(f'{point["label"]}: invalid throw — {error}', flush=True)
                continue
            save()
            if args.prepare_only:
                continue
            point['status'] = 'running'; save()
            for i, case in enumerate(point['cases']):
                trial = output / f'{identifier}-throw-{i+1}'
                command = [sys.executable, str(script), '--output', str(trial), '--mode', 'gripper',
                           '--profile', args.profile,
                           '--arc', args.arc, '--gripper-lead', str(args.gripper_lead), '--target-pitch', str(args.home_pitch), '--bounces', str(args.bounces),
                           '--court-restitution', str(args.court_restitution),
                           '--court-tangent-ratio', str(args.court_tangent_ratio),
                           '--position', *map(str, position), '--target', *map(str, case['target']),
                           '--speed', str(speed), '--drag-coefficient', str(args.drag_coefficient),
                           '--camera-position', *map(str, args.camera_position), '--camera-hz', str(args.camera_hz), '--resolution', *map(str, args.resolution),
                           '--wall-timeout', str(args.wall_timeout), '--no-dashboard']
                print(f'{point["label"]}: throw {i+1}/5, seed {case["seed"]}', flush=True)
                run = subprocess.run(command)
                if run.returncode not in (0, 1) or not (trial / 'result.json').exists():
                    point.update(status='infrastructure_error', reason=f'Inspect {trial.name}/launch.log')
                    infrastructure_error = True; break
                point['trials'].append(trial.name); completed.append(trial)
                (trial / 'throw_case.json').write_text(json.dumps(case, indent=2)+'\n')
                save()
            if not infrastructure_error:
                point['status'] = 'complete'
                point['successes'] = sum(json.loads((output / name / 'result.json').read_text())['success'] for name in point['trials'])
                point['passed'] = point['successes'] == 5
            save()
            create_dashboard(completed, output / 'dashboard.html', manifest['points'])
            if infrastructure_error:
                return 2
    create_dashboard(completed, output / 'dashboard.html', manifest['points'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
