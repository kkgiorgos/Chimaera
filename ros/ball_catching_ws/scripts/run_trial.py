#!/usr/bin/env python3
"""Run one native trial, preserve records, and stop when scoring completes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from datetime import datetime, timezone


def implementation_fingerprint(workspace):
    """Identify the dirty source and actual installed native artifacts per trial."""
    digest = hashlib.sha256()
    for folder in ('src', 'scripts', 'dashboard'):
        for path in sorted((workspace/folder).rglob('*')):
            if not path.is_file() or (path.suffix not in ('.py', '.cpp', '.hpp', '.js', '.html', '.xml')
                                      and path.name != 'CMakeLists.txt'):
                continue
            digest.update(str(path.relative_to(workspace)).encode()+b'\0')
            digest.update(path.read_bytes()+b'\0')
    artifacts = {}
    for package, names in [('ball_catching_robot', ('perception', 'intercept', 'effort_control', 'gripper_control', 'validate_model')),
                           ('ball_catching_sim', ('libball_catching_host.so',))]:
        for name in names:
            path = workspace/'install'/package/'lib'
            path = path/name if name.endswith('.so') else path/package/name
            if path.is_file():
                artifacts[str(path.relative_to(workspace))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest.hexdigest(), artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['gripper', 'cup'], default='gripper')
    parser.add_argument('--profile', choices=['gentle', 'court', 'court-bounce'], default='gentle',
                        help='Nearby overhead toss, full-court challenge, or bounced court practice' )
    parser.add_argument('--position', type=float, nargs=3)
    parser.add_argument('--direction', type=float, nargs=3, help='Explicit direction instead of an aimed throw')
    parser.add_argument('--target', type=float, nargs=3, default=[.5, 0., .8])
    parser.add_argument('--speed', type=float)
    parser.add_argument('--arc', choices=['low', 'high'], help='Defaults to a gentle upward toss or low court arc')
    parser.add_argument('--target-orientation', '--home-orientation', dest='home_orientation',
                        choices=['upward', 'forward'], help='Preflight target orientation; home remains upright')
    parser.add_argument('--target-pitch', '--home-pitch', dest='home_pitch', type=float,
                        help='Target reachability-check axis elevation; does not change upright home')
    parser.add_argument('--retention', type=float)
    parser.add_argument('--no-court', action='store_true')
    parser.add_argument('--bounces', type=int, choices=range(5), help='Allowed physical court impacts; court-bounce defaults to two')
    parser.add_argument('--court-restitution', type=float, default=.745)
    parser.add_argument('--court-tangent-ratio', type=float, default=.6)
    parser.add_argument('--drag-coefficient', type=float, default=.55)
    parser.add_argument('--gripper-lead', type=float, help='Motor startup compensation in seconds; bounced court defaults to 0.025')
    parser.add_argument('--grip-friction', type=float, default=1.)
    parser.add_argument('--present', action='store_true')
    parser.add_argument('--no-dashboard', action='store_true', help='Use when an experiment generates one combined dashboard')
    parser.add_argument('--camera-position', type=float, nargs=3)
    parser.add_argument('--camera-hz', type=float, default=90.)
    parser.add_argument('--resolution', type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--wall-timeout', type=float, default=120.)
    args = parser.parse_args()
    court_profile = args.mode == 'gripper' and args.profile in ('court', 'court-bounce')
    court = court_profile and not args.no_court
    if args.bounces is None:
        args.bounces = 2 if args.profile == 'court-bounce' else 0
    if args.bounces and not court:
        parser.error('bounces require a gripper court profile')
    if args.gripper_lead is None:
        args.gripper_lead = .025 if args.profile == 'court-bounce' else 0.
    if args.resolution is None:
        args.resolution = [1280, 960] if args.profile == 'court-bounce' else [640, 480]
    if args.camera_position is None:
        args.camera_position = [-1., .6, 1.2] if args.profile == 'court-bounce' else [-.65, 0., 1.]
    if args.position is None:
        args.position = ([23.77, 0., 2.5] if court_profile else [.5, 0., 1.1]) if args.mode == 'gripper' else [2.08, .04, 2.]
    if args.speed is None:
        args.speed = (14. if args.profile == "court-bounce" else 20. if court_profile else 1.5) if args.mode == 'gripper' else 3.
    if args.retention is None:
        args.retention = .2 if args.mode == 'gripper' else 1.
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output directory must be empty or new')
    output.mkdir(parents=True, exist_ok=True)
    command = ['ros2', 'launch', 'ball_catching_sim', 'application.launch.py',
               f'output:={output}', f'gui:={str(args.gui).lower()}',
               'launch_position:=' + json.dumps(args.position),
               'launch_direction:=' + json.dumps(args.direction or [-1., 0., 0.]), f'launch_speed:={args.speed}',
               f'mode:={args.mode}', f'court:={str(court).lower()}',
               f'allowed_bounces:={args.bounces}', f'court_restitution:={args.court_restitution}',
               f'court_tangent_ratio:={args.court_tangent_ratio}',
               f'home_orientation:={args.home_orientation or ("forward" if court_profile else "upward")}',
               f'home_pitch:={args.home_pitch if args.home_pitch is not None else 30 if args.profile == "court-bounce" and args.home_orientation is None else "auto"}',
               f'arc:={args.arc or ("low" if court_profile else "high")}',
               f'aimed:={str(args.direction is None and args.mode == "gripper").lower()}',
               'target:=' + json.dumps(args.target),
               f'drag_coefficient:={args.drag_coefficient if args.mode == "gripper" else 0.}',
               f'gripper_motor_lead:={args.gripper_lead}', f'grip_friction:={args.grip_friction}', f'present:={str(args.present).lower()}',
               f'retention:={args.retention}', f'camera_hz:={args.camera_hz}',
               'camera_position:=' + json.dumps(args.camera_position),
               f'camera_width:={args.resolution[0]}', f'camera_height:={args.resolution[1]}',
               f'partition:=ball-catching-{os.getpid()}']
    environment = os.environ.copy()
    # Gazebo partitions alone do not isolate ROS topics between local trials.
    environment['ROS_DOMAIN_ID'] = str(30 + os.getpid() % 170)
    environment['ROS_LOG_DIR'] = str(output / 'ros_logs')
    # Rendering logs/cache belong to the trial, keeping headless workers isolated.
    environment['IGN_LOG_PATH'] = str(output / 'gazebo_logs')
    repository = Path(__file__).resolve().parents[3]
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repository,
                              capture_output=True, text=True)
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=repository,
                           capture_output=True, text=True)
    source_digest, artifacts = implementation_fingerprint(Path(__file__).resolve().parents[1])
    metadata = dict(implementation_sha256=source_digest, native_artifacts_sha256=artifacts, started_at=datetime.now(timezone.utc).isoformat(),
                    git_revision=revision.stdout.strip() if revision.returncode == 0 else None,
                    working_tree_dirty=bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
                    ros_distro=environment.get('ROS_DISTRO'),
                    ros_domain_id=environment['ROS_DOMAIN_ID'], command=command)
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    started = time.monotonic()
    result = None
    with (output / 'launch.log').open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=environment, start_new_session=True)
        try:
            while process.poll() is None:
                if (output / 'result.json').exists():
                    try:
                        result = json.loads((output / 'result.json').read_text())
                    except json.JSONDecodeError:
                        time.sleep(0.05)
                        continue
                    if not args.present or not result['success'] or (output / 'presentation_done.json').exists():
                        break
                if time.monotonic() - started > args.wall_timeout:
                    raise TimeoutError('Trial exceeded wall timeout; inspect launch.log')
                time.sleep(0.1)
            if result is None:
                raise RuntimeError(f'Application exited with {process.returncode}; inspect launch.log')
        finally:
            if process.poll() is None:
                # ROS launch forwards SIGINT to its children. Signaling the
                # whole group as well would deliver it twice during teardown.
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
    print(json.dumps(result, indent=2))
    presentation = None
    if (output/'presentation_done.json').exists():
        presentation = json.loads((output/'presentation_done.json').read_text())
        print('Presentation:', json.dumps(presentation))
    metadata.update(wall_seconds=time.monotonic() - started, result=result, presentation=presentation)
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if not args.no_dashboard:
        from report import create_dashboard
        create_dashboard([output], output / 'dashboard.html')
    return 0 if result['success'] and not (presentation and presentation.get('held') is False) else 1


if __name__ == '__main__':
    raise SystemExit(main())
