#!/usr/bin/env python3
"""Run one native trial, preserve records, and stop when scoring completes."""
import argparse
import hashlib
import json
import math
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
    for package, names in [('ball_catching_robot', ('perception', 'intercept', 'effort_control', 'validate_model')),
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
    parser.add_argument('--position', type=float, nargs=3, default=[2.08, .04, 2.])
    parser.add_argument('--direction', type=float, nargs=3, default=[-1., 0., 0.])
    parser.add_argument('--speed', type=float, default=3.)
    parser.add_argument('--no-dashboard', action='store_true', help='Use when an experiment generates one combined dashboard')
    parser.add_argument('--camera-position', type=float, nargs=3, default=[-.65, 0., 1.])
    parser.add_argument('--camera-hz', type=float, default=90.)
    parser.add_argument('--image-age-periods', type=float, default=2.)
    parser.add_argument('--resolution', type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'), default=[640, 480])
    parser.add_argument('--control-hz', type=float, default=250.)
    parser.add_argument('--planner-hz', type=float, default=30.)
    parser.add_argument('--plan-interval', type=float, default=.06)
    parser.add_argument('--joint-hz', type=float, default=250.)
    parser.add_argument('--physics-step', type=float, default=.001)
    parser.add_argument('--warmup', type=float, default=0.)
    parser.add_argument('--cup-radius', type=float, default=.12)
    parser.add_argument('--cup-depth', type=float, default=.14)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--duration', type=float, default=2.,
                        help='Simulated seconds after launch before final evaluation (default: 2)')
    parser.add_argument('--wall-timeout', type=float, default=120.)
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error('duration must be finite and positive')
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output directory must be empty or new')
    output.mkdir(parents=True, exist_ok=True)
    command = ['ros2', 'launch', 'ball_catching_sim', 'application.launch.py',
               f'output:={output}', f'gui:={str(args.gui).lower()}',
               'launch_position:=' + json.dumps(args.position),
               'launch_direction:=' + json.dumps(args.direction), f'launch_speed:={args.speed}',
               f'duration:={args.duration}', f'camera_hz:={args.camera_hz}',
               'camera_position:=' + json.dumps(args.camera_position),
               f'camera_width:={args.resolution[0]}', f'camera_height:={args.resolution[1]}',
               f'partition:=ball-catching-{os.getpid()}']
    command += [f'{name}:={getattr(args, name)}' for name in
                ('control_hz', 'planner_hz', 'plan_interval', 'joint_hz', 'physics_step', 'warmup',
                 'cup_radius', 'cup_depth', 'image_age_periods')]
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
    metadata.update(wall_seconds=time.monotonic() - started, result=result)
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if not args.no_dashboard:
        from report import create_dashboard
        create_dashboard([output], output / 'dashboard.html')
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
