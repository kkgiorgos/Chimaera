"""Verify the real host's ballistic launch through paused Fortress updates.

This uses native physics and synthetic readiness flags; no robot computation,
gem5, rendering, or Chimaera transport is involved.
"""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

import rclpy
from std_msgs.msg import Bool
from sensor_msgs.msg import JointState

from ball_catching_sim.scene import generate


STEP = .0005
POSITION = [1.8, .04, 3.]
VELOCITY = [-3 / math.sqrt(5), 0., 6 / math.sqrt(5)]


def check(root, plugin_directory, mode, require_validation):
    root.mkdir(parents=True)
    world, _, _, _ = generate(root, launch_position=POSITION,
        launch_direction=[-1., 0., 2.], launch_speed=3., physics_step=STEP,
        joint_hz=1 / STEP, warmup=.002, duration=.025, output=str(root))
    tree = ET.parse(world)
    scene = tree.getroot().find('world')
    # Rendering is irrelevant to a one-shot physics command regression.
    for model in list(scene.findall('model')):
        if model.get('name') in ('left_camera', 'right_camera'):
            scene.remove(model)
    for plugin in list(scene.findall('plugin')):
        if plugin.get('name', '').endswith(('::Sensors', '::SceneBroadcaster', '::Contact')):
            scene.remove(plugin)
    if mode == 'blocked':
        # A separate Gazebo controller overrides the ball's velocity to zero.
        # The host must reject that physical throw before task scoring.
        ball = next(model for model in scene.findall('model')
                    if model.get('name') == 'tennis_ball')
        ET.SubElement(ball, 'plugin', filename='ignition-gazebo-velocity-control-system',
            name='ignition::gazebo::systems::VelocityControl')
    tree.write(world)
    environment = os.environ | dict(IGN_IP='127.0.0.1',
        IGN_PARTITION=f'ball-launch-{os.getpid()}-{mode}',
        IGN_GAZEBO_SYSTEM_PLUGIN_PATH=str(plugin_directory),
        ROS_LOG_DIR=str(root / 'ros_logs'))
    node = rclpy.create_node('launch_physics_check_' + mode)
    ready = node.create_publisher(Bool, '/robot/ready', 1)
    camera = node.create_publisher(Bool, '/robot/perception_ready', 1)
    times = []
    subscription = node.create_subscription(JointState, '/robot/joint_states',
        lambda msg: times.append(msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9),
        rclpy.qos.qos_profile_sensor_data)

    def spin_until(condition, timeout=10):
        deadline = time.monotonic() + timeout
        while not condition():
            if process.poll() is not None:
                raise RuntimeError('Gazebo exited; inspect ' + str(root / 'gazebo.log'))
            if time.monotonic() >= deadline:
                raise TimeoutError('Native launch check timed out: ' + mode)
            rclpy.spin_once(node, timeout_sec=.01)

    def control(request):
        reply = subprocess.run(['ign', 'service', '-s', '/world/ball_catching/control',
            '--reqtype', 'ignition.msgs.WorldControl', '--reptype', 'ignition.msgs.Boolean',
            '--timeout', '3000', '--req', request], env=environment,
            capture_output=True, text=True, timeout=5)
        if reply.returncode or 'data: true' not in reply.stdout:
            raise RuntimeError('World control failed: ' + reply.stdout + reply.stderr)

    with (root / 'gazebo.log').open('w') as log:
        process = subprocess.Popen(['ign', 'gazebo', '-s', str(world)], env=environment,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            spin_until(lambda: ready.get_subscription_count() and camera.get_subscription_count())
            ready.publish(Bool(data=True))
            camera.publish(Bool(data=True))
            # Allow the host's independent ROS executor to accept readiness.
            time.sleep(.1)
            if mode == 'continuous':
                control('pause: false')
                spin_until(lambda: (root / 'result.json').exists())
            elif mode == 'batch':
                control('pause: true multi_step: 80')
                spin_until(lambda: (root / 'result.json').exists())
            elif mode == 'blocked':
                control('pause: true multi_step: 80')
                spin_until(lambda: (root / 'launch_validation.json').exists())
            else:
                for _ in range(80):
                    if (root / 'result.json').exists():
                        break
                    previous = times[-1] if times else -STEP
                    control('pause: true multi_step: 1')
                    spin_until(lambda: times and times[-1] > previous + STEP / 2)
                    # Real paused callbacks between each completed physics step.
                    time.sleep(.03)
                assert (root / 'result.json').exists(), 'Scoring did not complete'
        finally:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            node.destroy_node()

    if mode == 'blocked':
        validation = json.loads((root / 'launch_validation.json').read_text())
        record = dict(mode=mode,
            valid=validation['valid'] is False and not (root / 'result.json').exists(),
            launch_validation=validation, scorer_result_present=(root / 'result.json').exists(),
            output=str(root))
        (root / 'check.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record), flush=True)
        return record

    launch = json.loads((root / 'launch.json').read_text())['launch_time']
    rows = [{k: float(v) for k, v in row.items()}
            for row in csv.DictReader((root / 'ground_truth.csv').open())]
    rows = [row for row in rows if row['launched']]
    assert len(rows) >= 20
    pose_errors, velocity_errors = [], []
    for row in rows:
        elapsed = row['time'] - launch + STEP
        expected_position = [POSITION[i] + VELOCITY[i] * elapsed for i in range(3)]
        expected_position[2] -= .5 * 9.81 * elapsed * (elapsed + STEP)
        expected_velocity = [*VELOCITY]
        expected_velocity[2] -= 9.81 * elapsed
        pose_errors.append(max(abs(row['ball_' + axis] - value)
            for axis, value in zip('xyz', expected_position)))
        velocity_errors.append(max(abs(row['ball_v' + axis] - value)
            for axis, value in zip('xyz', expected_velocity)))
    validation_path = root / 'launch_validation.json'
    validation = json.loads(validation_path.read_text()) if validation_path.exists() else None
    record = dict(mode=mode, physics_step=STEP, samples=len(rows),
        valid=(max(pose_errors) < 2e-6 and max(velocity_errors) < 2e-6
               and (not require_validation or (validation is not None and validation['valid']))),
        launch_validation=validation,
        maximum_position_error_m=max(pose_errors), maximum_velocity_error_mps=max(velocity_errors),
        output=str(root))
    (root / 'check.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', action='append', choices=('continuous', 'batch', 'step', 'blocked'))
    parser.add_argument('--require-validation', action='store_true')
    args = parser.parse_args()
    os.environ['ROS_DOMAIN_ID'] = '197'
    os.environ['ROS_LOCALHOST_ONLY'] = '1'
    root = args.output.resolve() / str(time.time_ns())
    rclpy.init()
    try:
        records = [check(root / mode, args.plugin_dir.resolve(), mode, args.require_validation)
                   for mode in args.mode or ('continuous', 'batch', 'step', 'blocked')]
    finally:
        rclpy.shutdown()
    (root / 'summary.json').write_text(json.dumps(records, indent=2) + '\n')
    return 0 if all(record['valid'] for record in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
