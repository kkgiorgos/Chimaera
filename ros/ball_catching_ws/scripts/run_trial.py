#!/usr/bin/env python3
"""Run one native trial, preserve records, and stop when scoring completes."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--position', type=float, nargs=3, default=[2.08, 0.04, 2.0])
    parser.add_argument('--direction', type=float, nargs=3, default=[-1., 0., 0.])
    parser.add_argument('--speed', type=float, default=3.)
    parser.add_argument('--initial-pose', type=float, nargs=7)
    parser.add_argument('--retention', type=float, default=1.)
    parser.add_argument('--camera-hz', type=float, default=90.)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--wall-timeout', type=float, default=120.)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output directory must be empty or new')
    output.mkdir(parents=True, exist_ok=True)
    command = ['ros2', 'launch', 'ball_catching_sim', 'application.launch.py',
               f'output:={output}', f'gui:={str(args.gui).lower()}',
               'launch_position:=' + json.dumps(args.position),
               'launch_direction:=' + json.dumps(args.direction), f'launch_speed:={args.speed}',
               f'retention:={args.retention}', f'camera_hz:={args.camera_hz}',
               f'partition:=ball-catching-{os.getpid()}']
    if args.initial_pose:
        command += ['initial_pose:=' + json.dumps(args.initial_pose)]
    environment = os.environ.copy()
    # Gazebo partitions alone do not isolate ROS topics between local trials.
    environment['ROS_DOMAIN_ID'] = str(30 + os.getpid() % 170)
    environment['ROS_LOG_DIR'] = str(output / 'ros_logs')
    # Rendering logs/cache belong to the trial, keeping headless workers isolated.
    environment['IGN_LOG_PATH'] = str(output / 'gazebo_logs')
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
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
