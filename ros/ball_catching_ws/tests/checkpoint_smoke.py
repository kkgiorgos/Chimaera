#!/usr/bin/env python3
"""Fast real Linux/gem5/ROS/Gazebo checkpoint capture and restore fixture.

Use containers/ball-catching/checkpoint-smoke.py for the complete lifecycle.
Only readiness computation is synthetic; bridges, simulator configuration,
host replay, pressure/throw barriers and checkpoint integrity are production code.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'ros/ball_catching_ws/scripts'))
from run_memory_suite import load_config, plan, write_json
from readiness_checkpoint import freeze, key, provenance, validate


def build(output):
    """Compile current production bridges into an isolated overlay."""
    overlay = output / 'overlay'
    subprocess.run(['cmake', '-S', str(ROOT / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge'),
        '-B', str(output / 'bridge-build'), '-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_INSTALL_PREFIX=' + str(overlay), '-DBUILD_TESTING=OFF',
        '-DBUILD_GAZEBO_BRIDGE=ON', '-DGEM5_TRANSPORT_ROOT=' + str(ROOT / 'gem5-transport'),
        '-DGEM5_ROOT=/src/gem5',
        '-DGEM5_M5_LIBRARY=/opt/chimaera/gem5/util/m5/build/x86/out/libm5.a'], check=True)
    subprocess.run(['cmake', '--build', str(output / 'bridge-build'), '--parallel', '2',
                    '--target', 'install'], check=True)
    source = output / 'fixture-source'
    source.mkdir()
    (source / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.22)
project(checkpoint_fixture LANGUAGES CXX)
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
add_executable(checkpoint_fixture "''' + str(Path(__file__).with_name('checkpoint_fixture.cpp')) + '''")
ament_target_dependencies(checkpoint_fixture rclcpp std_msgs)
''')
    subprocess.run(['cmake', '-S', str(source), '-B', str(output / 'fixture-build'),
                    '-DCMAKE_BUILD_TYPE=Release'], check=True)
    subprocess.run(['cmake', '--build', str(output / 'fixture-build'), '--parallel', '2'], check=True)


def configuration(output):
    source = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    data = json.loads(source.read_text())
    data['ramulator_config'] = str((source.parent / data['ramulator_config']).resolve())
    data['fixed'].update(duration=.100, warmup=.001, pressure_warmup=.001,
                         camera_width=64, camera_height=64)
    path = output / 'config.json'
    write_json(path, data)
    return load_config(path)


def routes():
    return dict(version=1, topics=[dict(host_topic=topic, guest_topic=topic, type=kind,
        direction=direction, coalesce=True, qos=dict(history='keep_last', depth=1,
            reliability='reliable', durability='volatile'))
        for topic, kind, direction in (
            ('/robot/ready', 'std_msgs/msg/Bool', 'guest_to_host'),
            ('/robot/perception_ready', 'std_msgs/msg/Bool', 'guest_to_host'),
            ('/smoke/request', 'std_msgs/msg/UInt64', 'host_to_guest'),
            ('/smoke/response', 'std_msgs/msg/UInt64', 'guest_to_host'))])


def bundle(output, prepared):
    import shutil
    staged = output / 'guest-root'
    guest = staged / 'opt/chimaera/checkpoint-smoke'
    guest.mkdir(parents=True)
    shutil.copy2(output / 'overlay/lib/chimaera_ros_bridge/guest_bridge', guest / 'guest_bridge')
    shutil.copy2(output / 'fixture-build/checkpoint_fixture', guest / 'fixture')
    write_json(guest / 'routes.json', routes())
    script = '''#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=72 ROS_LOCALHOST_ONLY=1 ROS_LOG_DIR=/tmp/smoke-ros
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
cd /opt/chimaera/checkpoint-smoke
./fixture &
exec ./guest_bridge --ros-args -p config_file:=/opt/chimaera/checkpoint-smoke/routes.json \
  -p wait_for_application:=true -p startup_timeout_s:=60 -p checkpoint_control:=true \
  -p simulation_time_from_readfile:=true -p m5ops:=address
'''
    (guest / 'start').write_text(script)
    (guest / 'start').chmod(0o755)
    path = prepared / 'guest.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        archive.add(staged / 'opt', arcname='opt')
    return path


def terminate(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def run(output, mode, nonce, readiness=None):
    from ament_index_python.packages import get_package_prefix
    from ball_catching_sim.scene import generate
    import rclpy
    from std_msgs.msg import UInt64

    data = configuration(output) if mode == 'capture' else load_config(output / 'config.json')
    point = plan(data)[0]
    prepared = output / 'inputs/prepared'
    if mode == 'capture':
        prepared.mkdir(parents=True)
        guest_bundle = bundle(output, prepared)
        directory = prepared / 'checkpoints' / key(data, point)
    else:
        guest_bundle = readiness / 'guest.tar.gz'
        identity = provenance('/opt/chimaera/gem5/build/X86/gem5.opt', '/assets/kernel',
                              '/assets/guest.img', guest_bundle)
        checkpoint = validate(readiness / 'checkpoints', data, point, identity)
        directory = output / f'restore-{nonce}'
    directory.mkdir(parents=True)
    socket = '/tmp/chimaera_time.sock'
    world, _, _, _ = generate(directory, physics_step=.0005, joint_hz=2000,
        camera_width=64, camera_height=64, warmup=.001, duration=.100,
        output=str(directory), checkpoint_gate=True, detailed_timing_socket=socket,
        launch_speed=3 if mode == 'capture' else 4)
    document = ET.parse(world)
    scene = document.getroot().find('world')
    for model in list(scene.findall('model')):
        if model.get('name') in ('left_camera', 'right_camera'):
            scene.remove(model)
    for plugin in list(scene.findall('plugin')):
        if plugin.get('name', '').endswith(('::Sensors', '::SceneBroadcaster', '::Contact')):
            scene.remove(plugin)
    document.write(world)
    route_file = directory / 'routes.json'
    write_json(route_file, routes())
    start = directory / 'start-guest.sh'
    start.write_text('''#!/bin/bash
set -e
printf "CHIMAERA_ONLINE_CPUS=%s\\n" "$(getconf _NPROCESSORS_ONLN)"
tar -xzf /tmp/chimaera-ball.tar.gz -C /
exec /opt/chimaera/checkpoint-smoke/start
''')
    start.chmod(0o755)
    execution = dict(hardware=data['hardware'],
        aggressor=dict(data['aggressor'], pattern='none' if mode == 'capture' else 'stream',
                       interval=1, gbps=0 if mode == 'capture' else 1),
        ramulator_config=data['ramulator_config'])
    execution['checkpoint_prepare' if mode == 'capture' else 'checkpoint_restore'] = str(
        directory if mode == 'capture' else checkpoint)
    execution_path = directory / 'execution.json'
    write_json(execution_path, execution)
    plugin = str(Path(get_package_prefix('ball_catching_sim')) / 'lib')
    environment = os.environ | dict(IGN_PARTITION='checkpoint-smoke', IGN_IP='127.0.0.1',
        IGN_GAZEBO_SYSTEM_PLUGIN_PATH=plugin, ROS_DOMAIN_ID='193', ROS_LOCALHOST_ONLY='1',
        ROS_LOG_DIR=str(directory / 'ros-logs'))
    gem5 = ['/opt/chimaera/gem5/build/X86/gem5.opt', '--outdir=' + str(directory / 'gem5'),
        '-p', str(ROOT / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge/config'),
        str(ROOT / 'ros/ball_catching_ws/scripts/gem5_ball_catching.py'),
        '--gem5-root', '/opt/chimaera/gem5', '--experiment', str(execution_path),
        '--bundle', str(guest_bundle), '--image', '/assets/guest.img', '--kernel', '/assets/kernel',
        '--managed-shutdown', '--guest-file', '/tmp/chimaera-ball-start=' + str(start)]
    bridge = [str(output / 'overlay/lib/chimaera_ros_bridge/gazebo_host_bridge'), '--ros-args',
        '-p', 'config_file:=' + str(route_file), '-p', 'timing_socket:=' + socket,
        '-p', 'gazebo_world:=ball_catching', '-p', 'interval_us:=500', '-p', 'poll_us:=100',
        '-p', 'physics_step_ns:=500000', '-p', 'startup_timeout_s:=90', '-p', 'status_bar:=false',
        '-p', 'steps:=56', '-p', 'timing_file:=' + str(directory / 'timing.csv'),
        '-p', 'checkpoint_output:=' + str(directory)]
    if mode == 'capture':
        bridge += ['-p', 'checkpoint_capture:=' + str(directory / 'host-replay.bin')]
    else:
        bridge += ['-p', 'checkpoint_replay:=' + str(checkpoint / 'host-replay.bin'),
                   '-p', 'checkpoint_directory:=' + str(checkpoint), '-p', 'pressure_warmup_ns:=1000000']
        if Path('/dev/kvm').exists():
            raise RuntimeError('Restore must run in a container without /dev/kvm')
    os.environ.update({name: environment[name] for name in (
        'ROS_DOMAIN_ID', 'ROS_LOCALHOST_ONLY', 'ROS_LOG_DIR')})
    rclpy.init()
    client = rclpy.create_node('checkpoint_smoke_client')
    publisher = client.create_publisher(UInt64, '/smoke/request', 1)
    responses = []
    subscription = client.create_subscription(UInt64, '/smoke/response',
        lambda message: responses.append(message.data), 1)
    timer = client.create_timer(.05, lambda: publisher.publish(UInt64(data=nonce)))
    started = time.monotonic()
    children, logs = [], []
    try:
        for name, command in [('gazebo', ['ign', 'gazebo', '-s', str(world)]),
                              ('bridge', bridge), ('gem5', gem5)]:
            log = (directory / (name + '.log')).open('w')
            logs.append(log)
            children.append(subprocess.Popen(command, env=environment,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
        host = children[1]
        while host.poll() is None:
            rclpy.spin_once(client, timeout_sec=.05)
            if time.monotonic() - started > 180:
                raise TimeoutError(f'{mode} exceeded 180 wall seconds; inspect {directory}')
            if children[0].poll() is not None or children[2].poll() is not None:
                # Allow the bridge to finish its final QUIT after gem5 exits.
                if not (directory / 'checkpoint.json').exists() and mode == 'capture':
                    raise RuntimeError('Capture component exited before checkpoint completion')
        if host.returncode:
            raise RuntimeError(f'Host bridge exited {host.returncode}; inspect {directory / "bridge.log"}')
        children[2].wait(timeout=15)
        if children[2].returncode:
            raise RuntimeError(f'gem5 exited {children[2].returncode}; inspect {directory / "gem5.log"}')
        for _ in range(10):
            rclpy.spin_once(client, timeout_sec=.02)
        expected = ((1 if mode == 'capture' else 2) << 32) | nonce
        if expected not in responses:
            raise RuntimeError(f'No fresh stateful response {expected}; observed {responses[-10:]}')
        if mode == 'capture':
            if not (directory / 'checkpoint.json').is_file() or (directory / 'launch.json').exists():
                raise RuntimeError('Capture must save without launching')
            identity = provenance('/opt/chimaera/gem5/build/X86/gem5.opt', '/assets/kernel',
                                  '/assets/guest.img', guest_bundle)
            freeze(prepared / 'checkpoints', identity, [directory.name])
            validate(prepared / 'checkpoints', data, point, identity)
        else:
            for name in ('checkpoint-release', 'gem5/pressure_start.json', 'gem5/measurement_start.json',
                         'launch.json', 'launch_validation.json'):
                if not (directory / name).is_file():
                    raise RuntimeError('Missing restored execution evidence: ' + name)
            if not json.loads((directory / 'launch_validation.json').read_text())['valid']:
                raise RuntimeError('Restored physical throw failed validation')
            if 'Replayed ' not in (directory / 'bridge.log').read_text():
                raise RuntimeError('Host replay did not run')
            pressure = json.loads((directory / 'gem5/pressure_start.json').read_text())['tick']
            measurement = json.loads((directory / 'gem5/measurement_start.json').read_text())['tick']
            if measurement - pressure < 1_000_000_000:
                raise RuntimeError('Pressure warmup did not advance gem5 for its configured 1 ms')
        import csv
        with (directory / 'timing.csv').open() as stream:
            simulated = sum(float(row['sim_seconds']) for row in csv.DictReader(stream))
        metadata = json.loads((directory / 'checkpoint.json' if mode == 'capture'
                               else checkpoint / 'checkpoint.json').read_text())
        if mode == 'capture':
            detailed = (metadata['saved_tick'] - metadata['time_origin_ns'] * 1000) / 1e12
        else:
            stopped = re.findall(r'^finalTick\s+(\d+)\b',
                                 (directory / 'gem5/stats.txt').read_text(), re.MULTILINE)
            if not stopped:
                raise RuntimeError('Missing final restored simulator tick')
            detailed = (int(stopped[-1]) - metadata['saved_tick']) / 1e12
        if not 0 <= detailed <= .030:
            raise RuntimeError('Smoke test exceeded its 30 ms detailed simulation budget')
        result = dict(mode=mode, nonce=nonce, expected_response=expected,
            synchronized_seconds=simulated, detailed_seconds=detailed,
            pressure_warmup_seconds=None if mode == 'capture' else (measurement - pressure) / 1e12,
            wall_seconds=time.monotonic() - started,
            directory=str(directory), kvm=Path('/dev/kvm').exists())
        write_json(output / (mode + '-' + str(nonce) + '.json'), result)
        print(json.dumps(result), flush=True)
    finally:
        for child in reversed(children):
            terminate(child)
        for log in logs:
            log.close()
        with (directory / 'launch.log').open('w') as combined:
            for name in ('gazebo', 'bridge', 'gem5'):
                path = directory / (name + '.log')
                if path.is_file():
                    combined.write(f'\n[{name}]\n' + path.read_text(errors='replace'))
        client.destroy_node()
        rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['build', 'capture', 'restore'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--nonce', type=int, default=1)
    parser.add_argument('--readiness', type=Path)
    args = parser.parse_args()
    if args.phase == 'build':
        build(args.output)
    else:
        run(args.output, args.phase, args.nonce, args.readiness)


if __name__ == '__main__':
    main()
