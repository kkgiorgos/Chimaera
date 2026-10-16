"""Real Fortress/bridge capture and replay with a synthetic guest, without gem5."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_prefix
from rclpy.serialization import serialize_message
from std_msgs.msg import Bool
from ball_catching_sim.scene import generate


def receive(connection, size):
    result = bytearray()
    while len(result) < size:
        part = connection.recv(size - len(result))
        if not part:
            raise RuntimeError('Synthetic guest received an incomplete packet')
        result.extend(part)
    return bytes(result)


def exchange(payload):
    for packet in (struct.pack('>Q', len(payload)), payload):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(10)
            connection.connect('/tmp/chimaera_g2h.sock')
            connection.sendall(struct.pack('@Q', len(packet)) + packet)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect('/tmp/chimaera_h2g.sock')
        size = struct.unpack('>Q', receive(connection, 8))[0]
    if not size:
        return b''
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect('/tmp/chimaera_h2g.sock')
        return receive(connection, size)


def frame(route, ready):
    parts = [route[key] for key in ('host_topic', 'guest_topic', 'type', 'direction')]
    parts += ['keep_last', '1', 'reliable', 'volatile']
    key = ''.join(f'{len(part)}:{part}' for part in parts).encode()
    return b'CBR1' + struct.pack('>I', len(key)) + key + serialize_message(Bool(data=ready))


def run(root, plugin, prepared=None, tick=0):
    root.mkdir(parents=True)
    endpoint = f'/tmp/checkpoint-replay-{os.getpid()}-{root.name}.sock'
    world, _, _, _ = generate(root, physics_step=.0005, joint_hz=2000,
        warmup=.002, duration=.025, output=str(root), checkpoint_gate=True,
        detailed_timing_socket=endpoint, launch_speed=4 if prepared else 3)
    document = ET.parse(world)
    scene = document.getroot().find('world')
    for model in list(scene.findall('model')):
        if model.get('name') in ('left_camera', 'right_camera'):
            scene.remove(model)
    for entry in list(scene.findall('plugin')):
        if entry.get('name', '').endswith(('::Sensors', '::SceneBroadcaster', '::Contact')):
            scene.remove(entry)
    document.write(world)
    routes = [dict(host_topic=topic, guest_topic=topic, type='std_msgs/msg/Bool',
        direction='guest_to_host', qos=dict(history='keep_last', depth=1,
            reliability='reliable', durability='volatile'))
        for topic in ('/robot/ready', '/robot/perception_ready')]
    config = root / 'routes.json'
    config.write_text(json.dumps(dict(version=1, topics=routes)))
    commands, failures = [], []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(endpoint)
        server.listen()
        server.settimeout(30)
        def serve():
            nonlocal tick
            initialized = False
            try:
                while True:
                    with server.accept()[0] as connection, connection.makefile() as incoming:
                        command = incoming.readline().strip()
                        commands.append(command)
                        if command == 'STATUS':
                            reply = f'PAUSED {tick}'
                        elif command.startswith('STEP_TICKS '):
                            if not initialized:
                                startup = exchange(struct.pack('>Q', 0x4348494d43545232))
                                assert struct.unpack('>QQ', startup)[1] > 0
                                initialized = True
                                time.sleep(.2)
                            # During restored pressure warmup, readiness becomes
                            # false. This must not extend the fixed launch hold.
                            frames = [frame(route, prepared is None) for route in routes]
                            batch = struct.pack('>Q', len(frames)) + b''.join(
                                struct.pack('>Q', len(value)) + value for value in frames)
                            exchange(batch)
                            end = tick + int(command.split()[1])
                            reply = f'OK {tick} {end}'
                            tick = end
                        elif command == 'BENCHMARK_CHECKPOINT':
                            assert not prepared and not (root / 'launch.json').exists()
                            reply = f'CHECKPOINT {tick}'
                        elif command == 'BENCHMARK_PRESSURE':
                            assert prepared and (root / 'checkpoint-ready').exists()
                            reply = f'PRESSURE {tick}'
                        elif command == 'BENCHMARK_BEGIN':
                            assert prepared and (root / 'checkpoint-release').exists()
                            reply = f'READY {tick}'
                        elif command == 'QUIT':
                            connection.sendall(b'BYE\n')
                            return
                        else:
                            raise AssertionError(command)
                        connection.sendall((reply + '\n').encode())
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        environment = os.environ | dict(IGN_IP='127.0.0.1',
            IGN_PARTITION=f'checkpoint-replay-{os.getpid()}-{root.name}',
            IGN_GAZEBO_SYSTEM_PLUGIN_PATH=str(plugin), ROS_DOMAIN_ID='193',
            ROS_LOCALHOST_ONLY='1', ROS_LOG_DIR=str(root / 'ros_logs'))
        with (root / 'gazebo.log').open('w') as log:
            gazebo = subprocess.Popen(['ign', 'gazebo', '-s', str(world)], env=environment,
                stdout=log, stderr=subprocess.STDOUT)
            try:
                bridge = Path(get_package_prefix('chimaera_ros_bridge')) / 'lib/chimaera_ros_bridge/gazebo_host_bridge'
                command = [str(bridge), '--ros-args', '-p', f'config_file:={config}',
                    '-p', f'timing_socket:={endpoint}', '-p', 'gazebo_world:=ball_catching',
                    '-p', 'interval_us:=500', '-p', 'poll_us:=100', '-p', 'physics_step_ns:=500000',
                    '-p', 'startup_timeout_s:=15', '-p', 'status_bar:=false',
                    '-p', f'checkpoint_output:={root}', '-p', 'steps:=51']
                if prepared:
                    command += ['-p', f'checkpoint_replay:={prepared / "host-replay.bin"}',
                        '-p', f'checkpoint_directory:={prepared}', '-p', 'pressure_warmup_ns:=3000000']
                else:
                    command += ['-p', f'checkpoint_capture:={root / "host-replay.bin"}']
                result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=35)
                (root / 'bridge.log').write_text(result.stdout + result.stderr)
                assert result.returncode == 0, result.stdout + result.stderr
                thread.join(timeout=2)
                assert not thread.is_alive() and not failures, failures
            finally:
                gazebo.send_signal(signal.SIGINT)
                try:
                    gazebo.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gazebo.kill()
                    gazebo.wait()
    Path(endpoint).unlink()
    if prepared:
        assert commands[:2] == ['STATUS', 'BENCHMARK_PRESSURE'], commands
        assert 'BENCHMARK_BEGIN' in commands
        assert 'Replayed ' in result.stdout + result.stderr
        assert (root / 'result.json').is_file()
        assert json.loads((root / 'launch_validation.json').read_text())['valid'] is True
    else:
        assert 'BENCHMARK_CHECKPOINT' in commands
        assert not (root / 'launch.json').exists()
    return tick


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    root = arguments.output.resolve() / str(time.time_ns())
    tick = run(root / 'prepare', arguments.plugin_dir.resolve())
    run(root / 'restore', arguments.plugin_dir.resolve(), root / 'prepare', tick)
    print('Host checkpoint capture/replay, pressure hold, independent throw and ballistic validation passed')
