#!/usr/bin/env python3
"""Local, resumable ball-catching memory interference experiments."""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import shutil
import subprocess
import sys
import tarfile
import time

from benchmark_metrics import command_metrics, memory_metrics, read_stats, controller_counters
from benchmark_status import check_local_transport, guest_failure, host_failure

WORKSPACE = Path(__file__).resolve().parents[1]
REPOSITORY = WORKSPACE.parents[1]
SCRIPTS = WORKSPACE / 'scripts'
GUEST_ROOT = '/opt/chimaera/ball_catching'


def write_json(path, value):
    """Atomic files let readers safely inspect the running suite."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def execution_fingerprint():
    """Freeze runner, installed application and host timing/transport artifacts."""
    from ament_index_python.packages import get_package_prefix
    result = hashlib.sha256()
    paths = [SCRIPTS / name for name in ('run_memory_suite.py', 'run_trial.py',
             'gem5_ball_catching.py', 'calibrate_memory.py', 'benchmark_metrics.py',
             'benchmark_status.py')]
    for package in ('ball_catching_robot', 'ball_catching_sim', 'chimaera_ros_bridge'):
        root = Path(get_package_prefix(package))
        paths += [path for path in root.rglob('*') if path.is_file()
                  and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo')]
    for path in sorted(set(paths)):
        result.update(str(path).encode() + b'\0' + digest(path).encode() + b'\0')
    return result.hexdigest()


def positive(value, name, zero=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f'{name} must be finite and {"nonnegative" if zero else "positive"}')


def load_config(path, cpu_type=None):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if set(data) != {'version', 'fixed', 'hardware', 'ramulator_config', 'throws', 'rates',
                     'patterns', 'pressure', 'aggressor', 'calibration'} or data['version'] != 1:
        raise ValueError('Expected version 1 benchmark configuration and documented fields')
    for section in ('fixed', 'hardware', 'aggressor', 'calibration'):
        if not isinstance(data[section], dict):
            raise ValueError(f'{section} must be an object')
    # Older experiment files retain the original Timing CPU behaviour.
    data['hardware'].setdefault('cpu_type', 'timing')
    data['fixed'].setdefault('image_diagnostics', False)
    data['fixed'].setdefault('guest_dds_shm_bytes', 0)
    if cpu_type is not None:
        data['hardware']['cpu_type'] = cpu_type
    required = {
        'fixed': {'duration', 'warmup', 'physics_step', 'joint_hz', 'camera_position',
                  'camera_width', 'camera_height', 'image_age_periods', 'cup_radius', 'cup_depth',
                  'interval_us', 'poll_us', 'startup_timeout_s', 'wall_timeout_s', 'image_diagnostics',
                  'guest_dds_shm_bytes'},
        'hardware': {'cpu_type', 'num_cores', 'cpu_clock', 'l1d_size', 'l1i_size', 'l1_assoc', 'l2_size', 'l2_assoc'},
        'aggressor': {'read_percent', 'stride', 'max_pending', 'window', 'seed',
                     'backpressure', 'duty_percent', 'period'},
        'calibration': {'warmup_ns', 'duration_ns'},
    }
    for section, keys in required.items():
        if set(data[section]) != keys:
            raise ValueError(f'{section} fields must be: ' + ', '.join(sorted(keys)))
    fixed = data['fixed']
    if type(fixed['image_diagnostics']) is not bool:
        raise ValueError('image_diagnostics must be boolean')
    for key in ('duration', 'physics_step', 'joint_hz', 'interval_us', 'poll_us',
                'startup_timeout_s', 'wall_timeout_s', 'camera_width', 'camera_height',
                'cup_radius', 'cup_depth', 'image_age_periods'):
        positive(fixed[key], key)
    positive(fixed['warmup'], 'warmup', zero=True)
    for key in ('interval_us', 'poll_us', 'startup_timeout_s', 'camera_width', 'camera_height'):
        if type(fixed[key]) is not int:
            raise ValueError(key + ' must be an integer')
    step_ns = round(fixed['physics_step'] * 1e9)
    if step_ns < 1 or fixed['interval_us'] * 1000 % step_ns or not 0 < fixed['poll_us'] < fixed['interval_us']:
        raise ValueError('Exchange must contain whole physics steps and 0 < poll_us < interval_us')
    if fixed['joint_hz'] * fixed['physics_step'] > 1 + 1e-8:
        raise ValueError('Joint feedback exceeds the physics rate')
    payload = fixed['camera_width'] * fixed['camera_height'] * 3 + 4096
    if 2 * payload > 32 * 1024 * 1024:
        raise ValueError('One stereo pair exceeds the controller 32 MiB budget; consult before changing transport')
    if fixed['camera_width'] < 64 or fixed['camera_height'] < 64:
        raise ValueError('Camera dimensions must be at least 64')
    segment = fixed['guest_dds_shm_bytes']
    if (type(segment) is not int or not 0 <= segment <= 64 * 1024 * 1024
            or (segment and segment < 2 * payload + 65536)):
        raise ValueError('guest_dds_shm_bytes must be 0 or fit a stereo pair plus 64 KiB, at most 64 MiB')
    def vector(value, name, nonzero=False):
        if not isinstance(value, list) or len(value) != 3 or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in value):
            raise ValueError(name + ' needs three finite numbers')
        if nonzero and sum(x * x for x in value) < 1e-18:
            raise ValueError(name + ' must be nonzero')
    vector(fixed['camera_position'], 'camera_position')
    for section in ('throws', 'rates'):
        if not isinstance(data[section], list) or not data[section]:
            raise ValueError(section + ' must be a nonempty array')
        names = set()
        for entry in data[section]:
            keys = {'name', 'launch_position', 'launch_direction', 'launch_speed'} if section == 'throws' else {
                'name', 'camera_hz', 'planner_hz', 'control_hz', 'plan_interval'}
            if not isinstance(entry, dict) or set(entry) != keys:
                raise ValueError(f'{section} entries need fields: ' + ', '.join(sorted(keys)))
            name = entry['name']
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in names:
                raise ValueError(section + ' names must be unique simple identifiers')
            names.add(name)
            if section == 'throws':
                vector(entry['launch_position'], 'launch_position')
                vector(entry['launch_direction'], 'launch_direction', True)
                positive(entry['launch_speed'], 'launch_speed')
            else:
                for key in ('camera_hz', 'planner_hz', 'control_hz'):
                    positive(entry[key], key)
                positive(entry['plan_interval'], 'plan_interval', zero=True)
                if fixed['image_age_periods'] / entry['camera_hz'] > 3600:
                    raise ValueError('Image freshness budget must be at most one simulated hour')
                if max(entry['camera_hz'], entry['control_hz']) * fixed['physics_step'] > 1 + 1e-8:
                    raise ValueError('Update frequencies exceed the physics rate')
                if entry['control_hz'] > fixed['joint_hz']:
                    raise ValueError('Configure fixed joint feedback at least as fast as every control tuple')
    if not isinstance(data['patterns'], list) or not data['patterns'] or any(
            not isinstance(pattern, str) for pattern in data['patterns']) or len(set(data['patterns'])) != len(data['patterns']) or any(
            pattern not in ('stream', 'stride', 'random') for pattern in data['patterns']):
        raise ValueError('Patterns must be unique stream/stride/random values')
    if not isinstance(data['pressure'], list) or not data['pressure']:
        raise ValueError('Pressure must be a nonempty array')
    for value in data['pressure']:
        positive(value, 'pressure', zero=True)
    if 0 not in data['pressure'] or len(set(data['pressure'])) != len(data['pressure']):
        raise ValueError('Pressure levels must be unique and include zero for matching baselines')
    aggressor = data['aggressor']
    if aggressor['backpressure'] is not True or aggressor['duty_percent'] != 100:
        raise ValueError('This steady-interference study requires backpressure and 100% duty')
    for key in ('stride', 'max_pending', 'window', 'seed', 'period'):
        if type(aggressor[key]) is not int or aggressor[key] < 1:
            raise ValueError('Invalid aggressor ' + key)
    if (aggressor['stride'] % 64 or aggressor['window'] % 64
            or type(aggressor['read_percent']) is not int or not 0 <= aggressor['read_percent'] <= 100):
        raise ValueError('Aggressor alignment or read percentage is invalid')
    hardware = data['hardware']
    if hardware['cpu_type'] not in ('timing', 'o3'):
        raise ValueError('hardware cpu_type must be timing or o3')
    for key in ('num_cores', 'l1_assoc', 'l2_assoc'):
        if type(hardware[key]) is not int or hardware[key] < 1:
            raise ValueError('Invalid hardware ' + key)
    for key in ('warmup_ns', 'duration_ns'):
        if type(data['calibration'][key]) is not int or data['calibration'][key] < 1:
            raise ValueError('Invalid calibration duration')
    ramulator = (path.parent / data['ramulator_config']).resolve()
    memory = json.loads(ramulator.read_text())
    if memory.get('frontend', {}).get('impl') != 'External':
        raise ValueError('Ramulator needs an External frontend export')
    controllers = memory['memory_system']['controllers']
    peak = sum(controller['dram']['channel_width'] / 8 *
               controller['dram']['timing'][0] / 1000 for controller in controllers)
    if not controllers or any(controller['dram']['impl'] != 'DDR4' for controller in controllers):
        raise ValueError('This preset requires DDR4 controllers with 64-byte transactions')
    if any(controller['dram']['channel_width'] != 64 for controller in controllers):
        raise ValueError('This benchmark currently requires 64-bit DDR4 channels')
    capacity = sum(math.prod(c['dram']['org']['count']) * c['dram']['channel_width'] // 8 for c in controllers)
    if 3 * 1024**3 + aggressor['window'] > capacity:
        raise ValueError('Guest and aggressor windows exceed physical DRAM capacity')
    data['ramulator_config'] = str(ramulator)
    data['ramulator_sha256'] = digest(ramulator)
    data['peak_gbps'] = peak
    return data


def plan(data):
    points = []
    for throw, rates, pattern, pressure in itertools.product(
            data['throws'], data['rates'], data['patterns'], data['pressure']):
        # A disabled aggressor has one baseline per throw/rate tuple.
        if pressure == 0 and pattern != data['patterns'][0]:
            continue
        index = len(points)
        cpu_type = data['hardware']['cpu_type']
        points.append(dict(id=f'case-{index:03d}', cpu_type=cpu_type,
                           label=f'{cpu_type.upper()} · {throw["name"]} · {rates["name"]} · '
                           + ('baseline' if pressure == 0 else f'{pattern} {pressure:.0%}'),
                           throw=throw, rates=rates, pattern=pattern if pressure else 'none',
                           pressure=pressure, status='pending', trials=[], expected_throws=1))
    return points


def routes():
    result = []
    for topic, kind, direction, reliable in [
            ('/clock', 'rosgraph_msgs/msg/Clock', 'host_to_guest', False),
            ('/stereo/left/image_raw', 'sensor_msgs/msg/Image', 'host_to_guest', False),
            ('/stereo/right/image_raw', 'sensor_msgs/msg/Image', 'host_to_guest', False),
            ('/robot/joint_states', 'sensor_msgs/msg/JointState', 'host_to_guest', False),
            ('/robot/effort_command', 'sensor_msgs/msg/JointState', 'guest_to_host', False),
            ('/robot/ready', 'std_msgs/msg/Bool', 'guest_to_host', True),
            ('/robot/perception_ready', 'std_msgs/msg/Bool', 'guest_to_host', True)]:
        result.append(dict(host_topic=topic, guest_topic=topic, type=kind, direction=direction,
                           coalesce=topic == '/clock' or 'image_raw' in topic,
                           qos=dict(history='keep_last', depth=2 if 'image' in topic else 1,
                                    reliability='reliable' if reliable else 'best_effort', durability='volatile')))
    for route in result:
        if route['type'] == 'sensor_msgs/msg/Image':
            route['coalesce_group'] = 'stereo'
    return dict(version=1, topics=result)


def robot_command(point, calibration, image_age_periods=2.0, image_diagnostics=False):
    return ['ros2', 'launch', 'ball_catching_robot', 'robot.launch.py',
            'robot_file:=/tmp/chimaera-ball.urdf',
            'calibration:=' + json.dumps(calibration or {}),
            f'image_age_periods:={image_age_periods}',
            f'image_diagnostics:={str(image_diagnostics).lower()}',
            'gem5_clock_library:=' + GUEST_ROOT + '/lib/libchimaera_gem5_clock.so',
            'gem5_time_origin_file:=/tmp/chimaera-sim-time-origin.ns',
            *[f'{key}:={point["rates"][key]}' for key in ('control_hz', 'planner_hz', 'plan_interval', 'camera_hz')]]


def session(output, data, point, calibration=None):
    fixed = data['fixed']
    robot = robot_command(point, calibration, fixed['image_age_periods'], fixed['image_diagnostics'])
    guest_env = dict(LD_LIBRARY_PATH=GUEST_ROOT + '/lib:/opt/ros/humble/lib:/opt/ros/humble/lib/x86_64-linux-gnu',
                     RMW_IMPLEMENTATION='rmw_fastrtps_cpp')
    if fixed['guest_dds_shm_bytes']:
        guest_env['FASTRTPS_DEFAULT_PROFILES_FILE'] = GUEST_ROOT + '/dds.xml'
    return dict(version=1, routes=str(output / 'routes.json'),
        host=dict(domain_id=71, setup=['/opt/ros/humble/setup.bash'], processes=[]),
        guest=dict(domain_id=72, setup=['/opt/ros/humble/setup.bash',
                    GUEST_ROOT + '/app/share/ball_catching_robot/local_setup.bash'],
                   localhost_only=not bool(fixed['guest_dds_shm_bytes']), env=guest_env,
                   processes=[dict(name='robot', command=robot)]),
        bridge=dict(interval_us=fixed['interval_us'], poll_us=fixed['poll_us'],
                    timing_backend='gazebo', gazebo_world='ball_catching',
                    physics_step_ns=round(fixed['physics_step'] * 1e9),
                    max_serialized_bytes=fixed['camera_width'] * fixed['camera_height'] * 3 + 4096,
                    max_pending_messages=16, m5ops='instruction', wait_for_application=True,
                    max_image_age_ns=math.ceil(fixed['image_age_periods'] / point['rates']['camera_hz'] * 1e9),
                    image_diagnostics=fixed['image_diagnostics'],
                    simulation_time_from_readfile=True,
                    startup_timeout_s=fixed['startup_timeout_s'], status_bar=False),
        deploy=dict(guest_root=GUEST_ROOT,
                    install_trees=[dict(source=str(WORKSPACE / 'install/ball_catching_robot'), destination='app')]))


def guest_dds_profile(segment_bytes):
    # Humble's localhost mode appends a default SHM transport. Use an explicit
    # UDP loopback whitelist and one sized SHM transport instead.
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <transport_descriptors>
    <transport_descriptor>
      <transport_id>local_udp</transport_id><type>UDPv4</type>
      <interfaceWhiteList><address>127.0.0.1</address></interfaceWhiteList>
    </transport_descriptor>
    <transport_descriptor>
      <transport_id>image_shm</transport_id><type>SHM</type>
      <segment_size>{segment_bytes}</segment_size>
    </transport_descriptor>
  </transport_descriptors>
  <participant profile_name="image_participant" is_default_profile="true">
    <rtps>
      <userTransports>
        <transport_id>local_udp</transport_id><transport_id>image_shm</transport_id>
      </userTransports>
      <useBuiltinTransports>false</useBuiltinTransports>
    </rtps>
  </participant>
</profiles>
'''


def prepare_bundle(output, data, points):
    from chimaera_ros.manifest import load
    from chimaera_ros.runner import stage
    write_json(output / 'routes.json', routes())
    write_json(output / 'staging-session.json', session(output, data, points[0]))
    staged = output / 'staged'
    if staged.exists():
        staged = output / f'staged-{len(list(output.glob("staged*"))) + 1:03d}'
    root = stage(load(output / 'staging-session.json'), staged)
    if data['fixed']['guest_dds_shm_bytes']:
        (root / 'dds.xml').write_text(guest_dds_profile(data['fixed']['guest_dds_shm_bytes']))
    libraries = root / 'lib'
    libraries.mkdir()
    from ament_index_python.packages import get_package_prefix
    clock = Path(get_package_prefix('chimaera_ros_bridge')) / 'lib/libchimaera_gem5_clock.so'
    shutil.copyfile(clock, libraries / clock.name)
    # RMW is loaded dynamically and absent from application ldd output. Bundle
    # the selected plugin and its dependencies so DDS matches native validation.
    middleware = Path(get_package_prefix('rmw_fastrtps_cpp')) / 'lib/librmw_fastrtps_cpp.so'
    shutil.copyfile(middleware, libraries / middleware.name)
    binaries = [root / 'runtime/guest_bridge', middleware,
                *list((root / 'app/lib/ball_catching_robot').iterdir())]
    for binary in binaries:
        linked = subprocess.run(['ldd', str(binary)], check=True, capture_output=True, text=True).stdout
        if 'not found' in linked:
            raise ValueError('Missing runtime library for ' + str(binary))
        for line in linked.splitlines():
            match = re.search(r'=> (/\S+)', line)
            if not match:
                continue
            path = Path(match[1])
            if re.fullmatch(r'lib(c|m|pthread|dl|rt)\.so.*', path.name):
                continue
            target = libraries / path.name
            if not target.exists():
                shutil.copyfile(path, target)
    # Use each trial's injected session, rather than hard-coding its geometry/rates.
    (root / 'guest_start').write_text('#!/bin/bash\nset -e\ncd ' + shlex.quote(GUEST_ROOT) + '\n'
        + 'export PYTHONPATH=' + shlex.quote(GUEST_ROOT + '/runtime') + '\n'
        + 'rm -f /tmp/chimaera-sim-time-origin.ns\n'
        + 'exec python3 -m chimaera_ros.runner run /tmp/chimaera-ball-session.json --side guest --guest-binary '
        + shlex.quote(GUEST_ROOT + '/runtime/guest_bridge') + '\n')
    bundle = output / 'guest.tar.gz'
    with tarfile.open(bundle, 'w:gz', dereference=True) as archive:
        archive.add(staged / 'opt', arcname='opt')
    return bundle


def calibrate(output, data, gem5):
    result = {}
    for pattern in data['patterns']:
        directory = output / 'calibration' / pattern
        directory.mkdir(parents=True, exist_ok=True)
        record = directory / 'calibration.json'
        signature = hashlib.sha256(json.dumps({key: data[key] for key in
            ('ramulator_sha256', 'aggressor', 'calibration')}, sort_keys=True).encode()).hexdigest()
        if record.exists():
            cached = json.loads(record.read_text())
            if cached['signature'] != signature or cached['gem5_sha256'] != digest(gem5):
                raise ValueError('Calibration provenance changed; use a new suite directory')
            result[pattern] = cached
            continue
        command = [str(gem5), '--outdir=' + str(directory), str(SCRIPTS / 'calibrate_memory.py'),
                   '--config', data['ramulator_config'], '--pattern', pattern]
        for key in ('read_percent', 'stride', 'max_pending', 'window', 'seed'):
            command += ['--' + key.replace('_', '-'), str(data['aggressor'][key])]
        for key, value in data['calibration'].items():
            command += ['--' + key.replace('_', '-'), str(value)]
        print('Calibrating ' + pattern, flush=True)
        with (directory / 'run.log').open('w') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        stats = read_stats(directory / 'stats.txt')
        seconds = stats['simTicks'] / stats['simFreq']
        count = controller_counters(next(directory.glob('*.ramulator_stats.yaml')))
        bandwidth = sum(count.values()) * 64 / seconds / 1e9
        if bandwidth <= 0:
            raise ValueError('Calibration produced no physical DRAM traffic')
        cached = dict(signature=signature, gem5_sha256=digest(gem5), sustainable_gbps=bandwidth,
                      accepted_gbps=stats['system.memory.aggressorBytes'] / seconds / 1e9,
                      peak_fraction=bandwidth / data['peak_gbps'], pattern=pattern,
                      duration_seconds=seconds, method='aggressor-only, interval=1, bounded backpressure')
        write_json(record, cached)
        result[pattern] = cached
    return result


def timing_command(path, command, timeout=300):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall((command + '\n').encode())
        response = bytearray()
        while not response.endswith(b'\n'):
            block = connection.recv(256)
            if not block:
                raise RuntimeError('Timing server disconnected')
            response.extend(block)
        text = response.decode().strip()
        if text.startswith('ERROR'):
            raise RuntimeError(text)
        return text


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def native_trial(directory, data, point):
    fixed = data['fixed']
    throw = point['throw']
    command = [sys.executable, str(SCRIPTS / 'run_trial.py'), '--output', str(directory), '--no-dashboard']
    for option, key in [('position', 'launch_position'), ('direction', 'launch_direction')]:
        command += ['--' + option, *map(str, throw[key])]
    command += ['--speed', str(throw['launch_speed']), '--camera-position', *map(str, fixed['camera_position']),
                '--resolution', str(fixed['camera_width']), str(fixed['camera_height'])]
    for key in ('duration', 'physics_step', 'joint_hz', 'warmup', 'cup_radius', 'cup_depth', 'image_age_periods'):
        command += ['--' + key.replace('_', '-'), str(fixed[key])]
    for key in ('camera_hz', 'planner_hz', 'plan_interval', 'control_hz'):
        command += ['--' + key.replace('_', '-'), str(point['rates'][key])]
    command += ['--wall-timeout', str(min(180, fixed['wall_timeout_s']))]
    if fixed['image_diagnostics']:
        command += ['--image-diagnostics']
    completed = subprocess.run(command)
    if completed.returncode not in (0, 1) or not (directory / 'result.json').exists():
        raise RuntimeError('Native preflight failed; inspect launch.log')
    result = json.loads((directory / 'result.json').read_text())
    write_json(directory / 'benchmark.json', dict(mode='native_preflight', point=point, memory=None,
        commands=command_metrics(directory / 'commands.csv', result['launch_time'], result['result_time'],
                                 point['rates']['control_hz'])))


def gem5_trial(directory, output, data, point, args, calibration, bundle):
    from ball_catching_sim.scene import generate
    directory.mkdir()
    fixed = data['fixed']
    options = {key: fixed[key] for key in ('duration', 'warmup', 'physics_step', 'joint_hz',
                'camera_position', 'camera_width', 'camera_height', 'cup_radius', 'cup_depth')}
    options.update({key: value for key, value in point['throw'].items() if key != 'name'},
                   camera_hz=point['rates']['camera_hz'], output=str(directory))
    _, robot, _, camera = generate(directory, **options)
    guest_session = session(output, data, point, camera)
    guest_session['routes'] = GUEST_ROOT + '/routes.json'
    write_json(directory / 'guest-session.json', guest_session)
    start = directory / 'start-guest.sh'
    start.write_text('#!/bin/bash\nset -e\nprintf "CHIMAERA_ONLINE_CPUS=%s\\n" "$(getconf _NPROCESSORS_ONLN)"\n'
                     + 'tar -xzf /tmp/chimaera-ball.tar.gz -C /\nset +e\n'
                     + shlex.quote(GUEST_ROOT + '/guest_start')
                     + '\nresult=$?\nprintf "CHIMAERA_GUEST_APPLICATION_EXIT=%s\\n" "$result"\nexit "$result"\n')
    start.chmod(0o755)
    aggressor = dict(data['aggressor'], pattern=point['pattern'], interval=1,
                     gbps=point['pressure'] * calibration[point['pattern']]['sustainable_gbps']
                     if point['pressure'] else 0)
    frozen_memory = directory / 'ramulator.json'
    frozen_memory.write_bytes(Path(data['ramulator_config']).read_bytes())
    execution = dict(hardware=data['hardware'], aggressor=aggressor,
                     ramulator_config=str(frozen_memory), image=str(args.image), kernel=str(args.kernel),
                     gem5_root=str(args.gem5_root), bundle=str(bundle), options=options,
                     rates=point['rates'], routes=str(output / 'routes.json'),
                     fixed=fixed, scripts=str(SCRIPTS))
    write_json(directory / 'execution.json', execution)
    command = ['ros2', 'launch', 'ball_catching_sim', 'benchmark.launch.py',
               'execution:=' + str(directory / 'execution.json'), 'output:=' + str(directory)]
    environment = os.environ | {'ROS_DOMAIN_ID': '71', 'ROS_LOCALHOST_ONLY': '1',
                               'ROS_LOG_DIR': str(directory / 'ros_logs'),
                               'IGN_LOG_PATH': str(directory / 'gazebo_logs')}
    started = time.monotonic()
    next_guest_check = started
    reset = False
    roi = directory / 'gem5'
    timing_socket = Path('/tmp/chimaera_time.sock')
    with (directory / 'launch.log').open('w') as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now >= next_guest_check and not (directory / 'result.json').exists():
                    failure = guest_failure(roi / 'board.pc.com_1.device')
                    if failure:
                        raise RuntimeError(f'Guest application failed: {failure}; inspect guest serial')
                    next_guest_check = now + 1
                if not reset and (directory / 'launch.json').exists():
                    timing_command(timing_socket, 'BENCHMARK_RESET', fixed['wall_timeout_s'])
                    reset = True
                if (directory / 'result.json').exists():
                    if not reset:
                        raise RuntimeError('Task evaluated before memory window could start')
                    timing_command(timing_socket, 'BENCHMARK_DUMP', fixed['wall_timeout_s'])
                    break
                if time.monotonic() - started > fixed['wall_timeout_s']:
                    raise TimeoutError('Attempt exceeded wall time; inspect logs')
                time.sleep(.01)
            if not (directory / 'result.json').exists():
                failure = guest_failure(roi / 'board.pc.com_1.device') or host_failure(directory / 'launch.log')
                if failure:
                    raise RuntimeError('Co-simulation failed: ' + failure)
                raise RuntimeError('Co-simulation exited without task result; inspect launch.log and guest serial')
        finally:
            stop(process)
    result = json.loads((directory / 'result.json').read_text())
    metrics = memory_metrics(roi, data['peak_gbps'])
    write_json(directory / 'benchmark.json', dict(mode='gem5', point=point,
        memory=metrics, hardware=data['hardware'], aggressor=aggressor,
        calibrated_sustainable_gbps=calibration[point['pattern']]['sustainable_gbps'] if point['pressure'] else None,
        peak_gbps=data['peak_gbps'], wall_seconds=time.monotonic() - started,
        measurement_window='launch boundary to final evaluation; boundary quantization applies',
        commands=command_metrics(directory / 'commands.csv', result['launch_time'], result['result_time'],
                                 point['rates']['control_hz'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=WORKSPACE / 'experiments/memory_contention.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--gem5-root', type=Path, default=REPOSITORY / 'gem5')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--cpu-type', choices=('timing', 'o3'),
                        help='Override the fixed detailed CPU model for this suite')
    parser.add_argument('--case', action='append', help='Select plan case IDs; repeatable')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--native-preflight', action='store_true', help='Native baseline throws only; no memory measurements')
    parser.add_argument('--calibrate-only', action='store_true')
    parser.add_argument('--prepare-only', action='store_true',
                        help='Check artifacts and stage the guest; do not run calibration, gem5 or Gazebo')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.prepare_only and (args.native_preflight or args.calibrate_only):
        parser.error('--prepare-only cannot be combined with native execution or calibration')
    data = load_config(args.config, args.cpu_type)
    all_points = plan(data)
    points = all_points
    if args.case:
        unknown = set(args.case) - {point['id'] for point in points}
        if unknown:
            parser.error('Unknown cases: ' + ', '.join(sorted(unknown)))
        points = [point for point in points if point['id'] in args.case]
    if args.native_preflight:
        all_points = [point for point in all_points if point['pressure'] == 0]
        points = [point for point in points if point['pressure'] == 0]
    if not points:
        parser.error('No selected cases')
    if args.dry_run:
        print(json.dumps(dict(configuration=data, points=points), indent=2))
        return 0
    if not args.output:
        parser.error('--output is required unless --dry-run')
    if not (args.native_preflight or args.calibrate_only or args.prepare_only):
        try:
            check_local_transport()
        except RuntimeError as error:
            parser.error(str(error))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'experiment_manifest.json'
    identity = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    mode = 'native_preflight' if args.native_preflight else 'gem5'
    if manifest_path.exists():
        if not args.resume:
            parser.error('Output already contains a suite; use --resume or a new directory')
        manifest = json.loads(manifest_path.read_text())
        if manifest['configuration_sha256'] != identity or manifest['mode'] != mode:
            parser.error('Cannot resume a different configuration or execution mode')
        wanted = {point['id'] for point in points}
        points = [point for point in manifest['points'] if point['id'] in wanted]
    else:
        if any(output.iterdir()):
            parser.error('Output must be empty or new')
        manifest = dict(schema_version=1, configuration_sha256=identity, configuration=data,
                        mode=mode, points=all_points)
    (output / 'COLCON_IGNORE').touch()
    write_json(manifest_path, manifest)
    args.gem5_root = args.gem5_root.resolve()
    args.image = (args.image or args.gem5_root / 'resources/x86-ubuntu-22.04-ros-humble.img').resolve()
    args.kernel = (args.kernel or args.gem5_root / 'resources/x86-linux-kernel-5.15.180').resolve()
    gem5 = args.gem5_root / 'build/X86/gem5.opt'
    calibration = {}
    bundle = None
    if not args.native_preflight:
        for path in (args.image, args.kernel, gem5):
            if not path.is_file():
                raise ValueError('Missing artifact: ' + str(path))
        calibration = manifest.get('calibration', {}) if args.prepare_only else calibrate(output, data, gem5)
        if not args.calibrate_only:
            bundle = output / 'guest.tar.gz'
            if not bundle.exists():
                bundle = prepare_bundle(output, data, points)
        provenance = dict(gem5_sha256=digest(gem5), kernel_sha256=digest(args.kernel),
            execution_sha256=execution_fingerprint(),
            image=str(args.image), image_size=args.image.stat().st_size,
            image_mtime_ns=args.image.stat().st_mtime_ns, bundle_sha256=digest(bundle) if bundle else None,
            git_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPOSITORY, text=True).strip())
        prior = manifest.get('provenance', {})
        for key in ('gem5_sha256', 'kernel_sha256', 'execution_sha256', 'image', 'image_size', 'image_mtime_ns', 'bundle_sha256'):
            if prior.get(key) is not None and prior[key] != provenance[key]:
                raise ValueError('Cannot resume changed execution artifacts: ' + key)
        manifest['provenance'] = provenance
        manifest['calibration'] = calibration
    from report import create_dashboard
    def save():
        write_json(manifest_path, manifest)
        completed = [output / trial for point in manifest['points'] for trial in point['trials']]
        create_dashboard(completed, output / 'dashboard.html', manifest['points'])
    save()
    if args.prepare_only:
        write_json(output / 'cpu_check.json', dict(hardware=data['hardware'],
            ramulator_config=data['ramulator_config'],
            aggressor=dict(data['aggressor'], pattern='none', interval=1, gbps=0)))
        # Freeze the input and exact selected cases so handoff needs one command.
        frozen_config = output / 'prepared_config.json'
        write_json(frozen_config, {key: value for key, value in data.items()
                                  if key not in ('ramulator_sha256', 'peak_gbps')})
        command = [sys.executable, str(SCRIPTS / 'run_memory_suite.py'),
                   '--config', str(frozen_config), '--cpu-type', data['hardware']['cpu_type'],
                   '--gem5-root', str(args.gem5_root), '--image', str(args.image),
                   '--kernel', str(args.kernel), '--output', str(output), '--resume']
        for case in args.case or []:
            command += ['--case', case]
        run_script = output / 'run.sh'
        run_script.write_text('#!/usr/bin/env bash\nset -eo pipefail\ncd ' + shlex.quote(str(REPOSITORY))
            + '\nsource /opt/ros/humble/setup.bash\n'
            + 'source ' + shlex.quote(str(REPOSITORY / 'ros/chimaera_ros_ws/install/local_setup.bash')) + '\n'
            + 'source ' + shlex.quote(str(WORKSPACE / 'install/local_setup.bash')) + '\n'
            + 'exec ' + shlex.join(command) + '\n')
        run_script.chmod(0o755)
        print('Prepared ' + data['hardware']['cpu_type'] + ' guest bundle: ' + str(bundle))
        print('Run with: bash ' + shlex.quote(str(run_script)))
        return 0
    if args.calibrate_only:
        print(json.dumps(calibration, indent=2))
        return 0
    failed = False
    try:
        for point in points:
            if point['status'] == 'complete':
                continue
            point['status'] = 'running'
            attempts = sorted(output.glob(point['id'] + '-attempt-*'))
            directory = output / f'{point["id"]}-attempt-{len(attempts) + 1:03d}'
            save()
            print('Running ' + point['label'], flush=True)
            try:
                if args.native_preflight:
                    native_trial(directory, data, point)
                else:
                    gem5_trial(directory, output, data, point, args, calibration, bundle)
                point.update(status='complete', trials=[directory.name],
                             config=json.loads((directory / 'experiment.json').read_text()))
                result = json.loads((directory / 'result.json').read_text())
                print(('CATCH' if result['success'] else 'MISS') + ' · ' + point['label'], flush=True)
            except Exception as error:
                failed = True
                point.update(status='infrastructure_failure', reason=str(error))
                # Preserve the first transport/guest failure for consultation.
                save()
                print(str(error), file=sys.stderr)
                break
            save()
    except KeyboardInterrupt:
        failed = True
        point.update(status='interrupted')
        save()
    return 2 if failed else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
