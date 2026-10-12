import copy
import json
from pathlib import Path
import sys
import shutil
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_metrics import memory_metrics, percentile, command_metrics
from run_memory_suite import load_config, plan, routes, robot_command, native_trial

WORKSPACE = Path(__file__).resolve().parents[1]


def configuration(tmp_path):
    path = WORKSPACE / 'experiments/memory_contention.json'
    data = json.loads(path.read_text())
    data['ramulator_config'] = str((path.parent / data['ramulator_config']).resolve())
    return data


def load(tmp_path, data):
    path = tmp_path / 'suite.json'
    path.write_text(json.dumps(data))
    return load_config(path)


def test_default_has_one_binary_trial_per_point(tmp_path):
    data = load(tmp_path, configuration(tmp_path))
    points = plan(data)
    assert len(points) == 12
    assert data['peak_gbps'] == pytest.approx(51.2)
    assert all(point['expected_throws'] == 1 for point in points)
    assert len({point['id'] for point in points}) == len(points)


def test_timing_validation_preset_contains_only_the_qualified_baseline():
    data = load_config(WORKSPACE / 'experiments/memory_contention_timing_validation.json')
    points = plan(data)
    assert len(points) == 1 and points[0]['id'] == 'case-000'
    assert points[0]['pressure'] == 0 and points[0]['cpu_type'] == 'timing'
    assert points[0]['throw']['launch_speed'] == 3
    assert points[0]['rates']['camera_hz'] == 30
    assert (data['fixed']['camera_width'], data['fixed']['camera_height']) == (480, 360)
    assert data['fixed']['guest_dds_shm_bytes'] == 8 * 1024 * 1024
    assert data['fixed']['image_age_periods'] == 2
    assert data['fixed']['wall_timeout_s'] == 43200


def test_baselines_not_duplicated_across_patterns(tmp_path):
    raw = configuration(tmp_path)
    raw['patterns'] = ['stream', 'stride', 'random']
    points = plan(load(tmp_path, raw))
    assert len([point for point in points if point['pressure'] == 0]) == 4
    for point in points:
        matched = [base for base in points if base['pressure'] == 0
                   and base['throw'] == point['throw'] and base['rates'] == point['rates']]
        assert len(matched) == 1


@pytest.mark.parametrize('section,key,value', [
    ('fixed', 'duration', 0), ('fixed', 'warmup', -1),
    ('fixed', 'poll_us', 500), ('fixed', 'interval_us', 750),
    ('fixed', 'joint_hz', 100), ('fixed', 'camera_width', 4096),
    ('fixed', 'image_age_periods', 0), ('fixed', 'image_age_periods', float('inf')),
    ('fixed', 'image_diagnostics', 'true'),
    ('fixed', 'guest_dds_shm_bytes', True), ('fixed', 'guest_dds_shm_bytes', -1),
    ('fixed', 'guest_dds_shm_bytes', 65536), ('fixed', 'guest_dds_shm_bytes', 2.5),
    ('fixed', 'guest_dds_shm_bytes', 128 * 1024 * 1024),
    ('aggressor', 'backpressure', False), ('aggressor', 'stride', 1),
    ('aggressor', 'duty_percent', 50), ('hardware', 'num_cores', 0),
    ('hardware', 'cpu_type', 'kvm'), ('hardware', 'cpu_type', None)])
def test_invalid_experiments_fail_before_launch(tmp_path, section, key, value):
    raw = configuration(tmp_path)
    raw[section][key] = value
    if key == 'camera_width':
        raw['fixed']['camera_height'] = 4096
    with pytest.raises(ValueError):
        load(tmp_path, raw)


def test_cpu_override_changes_only_the_fixed_cpu_model(tmp_path):
    raw = configuration(tmp_path)
    del raw['hardware']['cpu_type']
    timing = load(tmp_path, raw)
    o3 = load_config(tmp_path / 'suite.json', cpu_type='o3')
    assert timing['hardware']['cpu_type'] == 'timing'
    assert o3['hardware']['cpu_type'] == 'o3'
    o3['hardware']['cpu_type'] = 'timing'
    assert o3 == timing


def test_prepare_only_stages_without_launching_and_rejects_a_different_cpu(tmp_path, monkeypatch):
    import run_memory_suite as runner
    import report
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(configuration(tmp_path)))
    gem5_root = tmp_path / 'gem5'
    artifacts = ['build/X86/gem5.opt', 'resources/x86-ubuntu-22.04-ros-humble.img',
                 'resources/x86-linux-kernel-5.15.180']
    for name in artifacts:
        path = gem5_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'artifact')
    output = tmp_path / 'prepared-o3'
    bundles = []
    def stage(directory, data, points):
        bundles.append((data['hardware']['cpu_type'], [point['id'] for point in points]))
        bundle = directory / 'guest.tar.gz'
        bundle.write_bytes(b'guest-payload')
        return bundle
    monkeypatch.setattr(runner, 'prepare_bundle', stage)
    monkeypatch.setattr(runner, 'check_local_transport', lambda: pytest.fail('Preparation must not require free sockets'))
    monkeypatch.setattr(runner, 'calibrate', lambda *args: pytest.fail('Preparation must not simulate calibration'))
    monkeypatch.setattr(runner, 'gem5_trial', lambda *args: pytest.fail('Preparation must not boot gem5'))
    monkeypatch.setattr(runner, 'execution_fingerprint', lambda: 'installed-artifacts')
    monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: 'test-revision\n')
    monkeypatch.setattr(report, 'create_dashboard', lambda *args: None)
    command = ['run_memory_suite.py', '--prepare-only', '--cpu-type', 'o3', '--case', 'case-003',
               '--config', str(config), '--gem5-root', str(gem5_root), '--output', str(output)]
    monkeypatch.setattr(sys, 'argv', command)
    assert runner.main() == 0
    assert bundles == [('o3', ['case-003'])]
    manifest = json.loads((output / 'experiment_manifest.json').read_text())
    assert manifest['configuration']['hardware']['cpu_type'] == 'o3'
    assert all(point['status'] == 'pending' and point['trials'] == [] for point in manifest['points'])
    assert json.loads((output / 'cpu_check.json').read_text())['aggressor']['gbps'] == 0
    script = (output / 'run.sh').read_text()
    assert '--cpu-type o3' in script and '--case case-003' in script and '--resume' in script
    assert '--prepare-only' not in script
    assert load_config(output / 'prepared_config.json') == load_config(config, 'o3')
    monkeypatch.setattr(sys, 'argv', command + ['--resume'])
    assert runner.main() == 0
    assert len(bundles) == 1
    changed = command.copy()
    changed[changed.index('o3')] = 'timing'
    monkeypatch.setattr(sys, 'argv', changed + ['--resume'])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert json.loads((output / 'experiment_manifest.json').read_text()) == manifest


def test_prepared_suite_resumes_into_a_real_trial(tmp_path, monkeypatch):
    # The previous test checks preparation in isolation; exercise the branch
    # that consumes its manifest and existing guest bundle next.
    import run_memory_suite as runner
    import report
    config = tmp_path / 'config.json'
    data = configuration(tmp_path)
    config.write_text(json.dumps(data))
    gem5_root = tmp_path / 'gem5'
    for name in ['build/X86/gem5.opt', 'resources/x86-ubuntu-22.04-ros-humble.img',
                 'resources/x86-linux-kernel-5.15.180']:
        path = gem5_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'artifact')
    output = tmp_path / 'o3'
    def stage(directory, *args):
        bundle = directory / 'guest.tar.gz'
        bundle.write_bytes(b'guest-payload')
        return bundle
    trials = []
    def trial(directory, output, data, point, args, calibration, bundle):
        trials.append((data['hardware']['cpu_type'], point['id'], bundle.read_bytes()))
        directory.mkdir()
        (directory / 'experiment.json').write_text('{}')
        (directory / 'result.json').write_text('{"success": true}')
    monkeypatch.setattr(runner, 'prepare_bundle', stage)
    monkeypatch.setattr(runner, 'gem5_trial', trial)
    monkeypatch.setattr(runner, 'check_local_transport', lambda: None)
    monkeypatch.setattr(runner, 'calibrate', lambda *args: {})
    monkeypatch.setattr(runner, 'execution_fingerprint', lambda: 'installed-artifacts')
    monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: 'test-revision\n')
    monkeypatch.setattr(report, 'create_dashboard', lambda *args: None)
    command = ['run_memory_suite.py', '--cpu-type', 'o3', '--case', 'case-003',
               '--config', str(config), '--gem5-root', str(gem5_root), '--output', str(output)]
    monkeypatch.setattr(sys, 'argv', command + ['--prepare-only'])
    assert runner.main() == 0
    monkeypatch.setattr(sys, 'argv', command + ['--resume'])
    assert runner.main() == 0
    assert trials == [('o3', 'case-003', b'guest-payload')]


@pytest.mark.parametrize('launched', [False, True])
def test_guest_failure_stops_attempt_without_waiting_for_wall_timeout(tmp_path, monkeypatch, launched):
    from types import SimpleNamespace
    import ball_catching_sim.scene
    import run_memory_suite as runner
    data = load(tmp_path, configuration(tmp_path))
    point = plan(data)[0]
    directory = tmp_path / 'case-000-attempt-001'
    stopped = []

    class RunningHost:
        def __init__(self, *args, **kwargs):
            (directory / 'gem5').mkdir()
            (directory / 'gem5/board.pc.com_1.device').write_text(
                'CHIMAERA_BOOTSTRAP_READY\r\nGuest bridge: trailing controller data\r\n')
            if launched:
                (directory / 'launch.json').write_text('{"launch_time":0.5}')

        def poll(self):
            return None  # The host keeps advancing after the guest crash.

    monkeypatch.setattr(ball_catching_sim.scene, 'generate', lambda *args, **kwargs: (
        None, tmp_path / 'robot.urdf', None, {'focal': 640}))
    monkeypatch.setattr(runner, 'session', lambda *args: {})
    monkeypatch.setattr(runner.subprocess, 'Popen', RunningHost)
    monkeypatch.setattr(runner, 'stop', lambda process: stopped.append(process))
    monkeypatch.setattr(runner, 'timing_command', lambda *args: pytest.fail('Do not reset/dump a failed guest'))
    monkeypatch.setattr(runner.time, 'monotonic', lambda: 0)
    args = SimpleNamespace(image=tmp_path / 'image', kernel=tmp_path / 'kernel',
                           gem5_root=tmp_path / 'gem5')
    with pytest.raises(RuntimeError, match='Guest application failed: Guest bridge: trailing controller data'):
        runner.gem5_trial(directory, tmp_path, data, point, args, {}, tmp_path / 'guest.tar.gz')
    assert len(stopped) == 1
    assert not (directory / 'result.json').exists()
    assert not (directory / 'benchmark.json').exists()


def test_host_truth_and_throw_settings_never_cross_bridge():
    topic_routes = routes()['topics']
    assert len(topic_routes) == 7
    assert sum(route['type'] == 'sensor_msgs/msg/Image' for route in topic_routes) == 2
    assert all(not route['host_topic'].startswith(('/evaluation', '/experiment')) for route in topic_routes)
    assert all('ball_state' not in route['host_topic'] for route in topic_routes)
    assert [route['host_topic'] for route in topic_routes if route['coalesce']] == [
        '/clock', '/stereo/left/image_raw', '/stereo/right/image_raw']
    assert [route['coalesce_group'] for route in topic_routes if 'coalesce_group' in route] == ['stereo', 'stereo']


def test_guest_launch_has_no_empty_ros_arguments(tmp_path):
    point = plan(load(tmp_path, configuration(tmp_path)))[0]
    command = robot_command(point, {'focal': 640})
    assert command[:4] == ['ros2', 'launch', 'ball_catching_robot', 'robot.launch.py']
    assert all(not item.endswith(':=') for item in command)
    assert 'output:=' not in command
    assert 'control_hz:=125.0' in command
    assert 'camera_hz:=60.0' in command
    assert 'image_age_periods:=2.0' in command
    assert any(arg.startswith('gem5_clock_library:=') for arg in command)


def test_each_tuple_gets_its_own_simulated_freshness_budget(tmp_path, monkeypatch):
    from run_memory_suite import session
    import ament_index_python.packages
    monkeypatch.setattr(ament_index_python.packages, 'get_package_prefix', lambda name: '/test/prefix')
    data = load(tmp_path, configuration(tmp_path))
    points = plan(data)
    reduced = session(tmp_path, data, points[0])
    standard = session(tmp_path, data, points[3])
    assert reduced['bridge']['max_image_age_ns'] == 33333334
    assert standard['bridge']['max_image_age_ns'] == 22222223
    assert reduced['bridge']['simulation_time_from_readfile'] is True


def test_older_configs_keep_default_dds_transport(tmp_path):
    from run_memory_suite import session
    raw = configuration(tmp_path)
    del raw['fixed']['guest_dds_shm_bytes']
    data = load(tmp_path, raw)
    assert data['fixed']['guest_dds_shm_bytes'] == 0
    guest = session(tmp_path, data, plan(data)[0])['guest']
    assert guest['localhost_only'] is True
    assert 'FASTRTPS_DEFAULT_PROFILES_FILE' not in guest['env']


@pytest.mark.parametrize('segment', [0, 4 * 1024 * 1024, 8 * 1024 * 1024])
def test_guest_bundle_contains_clock_and_clears_old_origin(tmp_path, monkeypatch, segment):
    monkeypatch.syspath_prepend(str(WORKSPACE.parent / 'chimaera_ros_ws/src/chimaera_ros_bridge'))
    import tarfile
    import ament_index_python.packages
    import chimaera_ros.runner
    from run_memory_suite import prepare_bundle, session, GUEST_ROOT
    from chimaera_ros.runner import plan as bridge_plan
    import xml.etree.ElementTree as ET
    prefix = tmp_path / 'bridge'
    clock = prefix / 'lib/libchimaera_gem5_clock.so'
    clock.parent.mkdir(parents=True)
    clock.write_bytes(b'clock-test-payload')
    middleware = prefix / 'lib/librmw_fastrtps_cpp.so'
    middleware.write_bytes(b'middleware-test-payload')
    monkeypatch.setattr(ament_index_python.packages, 'get_package_prefix', lambda name: str(prefix))
    def stage(manifest, staged):
        root = staged / GUEST_ROOT.lstrip('/')
        (root / 'runtime').mkdir(parents=True)
        (root / 'runtime/guest_bridge').write_bytes(b'bridge')
        binaries = root / 'app/lib/ball_catching_robot'
        binaries.mkdir(parents=True)
        (binaries / 'perception').write_bytes(b'perception')
        return root
    monkeypatch.setattr(chimaera_ros.runner, 'stage', stage)
    dependency = tmp_path / 'libtest.so'
    dependency.write_bytes(b'runtime-dependency')
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, stdout=f'libtest.so => {dependency} (0x1)\n'))
    data = load(tmp_path, configuration(tmp_path))
    data['fixed']['guest_dds_shm_bytes'] = segment
    output = tmp_path / 'bundle'
    output.mkdir()
    bundle = prepare_bundle(output, data, plan(data))
    with tarfile.open(bundle) as archive:
        root = GUEST_ROOT.lstrip('/')
        assert archive.extractfile(root + '/lib/libchimaera_gem5_clock.so').read() == clock.read_bytes()
        assert archive.extractfile(root + '/lib/librmw_fastrtps_cpp.so').read() == middleware.read_bytes()
        assert archive.extractfile(root + '/lib/libtest.so').read() == dependency.read_bytes()
        assert b'rm -f /tmp/chimaera-sim-time-origin.ns' in archive.extractfile(root + '/guest_start').read()
        guest = bridge_plan(session(output, data, plan(data)[0]), 'guest', '/test/bridge')
        assert guest['env']['ROS_LOCALHOST_ONLY'] == ('0' if segment else '1')
        assert guest['env']['RMW_IMPLEMENTATION'] == 'rmw_fastrtps_cpp'
        if segment:
            assert guest['env']['FASTRTPS_DEFAULT_PROFILES_FILE'] == GUEST_ROOT + '/dds.xml'
            profile = ET.fromstring(archive.extractfile(root + '/dds.xml').read())
            ns = {'dds': 'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
            transports = profile.findall('dds:transport_descriptors/dds:transport_descriptor', ns)
            assert len(transports) == 2
            udp, shm = transports
            assert udp.find('dds:type', ns).text == 'UDPv4'
            assert udp.find('dds:interfaceWhiteList/dds:address', ns).text == '127.0.0.1'
            assert shm.find('dds:type', ns).text == 'SHM'
            assert int(shm.find('dds:segment_size', ns).text) == segment
            participant = profile.find('dds:participant', ns)
            assert participant.attrib['is_default_profile'] == 'true'
            assert participant.find('dds:rtps/dds:useBuiltinTransports', ns).text == 'false'
        else:
            assert root + '/dds.xml' not in archive.getnames()


def test_native_preflight_preserves_custom_cup_geometry(tmp_path, monkeypatch):
    data = load(tmp_path, configuration(tmp_path))
    data['fixed'].update(cup_radius=.2, cup_depth=.18)
    point = plan(data)[0]
    directory = tmp_path / 'native'
    commands = []
    def launch(command):
        commands.append(command)
        directory.mkdir()
        (directory / 'result.json').write_text(json.dumps(dict(
            success=True, launch_time=1, result_time=3)))
        (directory / 'commands.csv').write_text('receipt_time,command_time\n1,1\n1.008,1.008\n')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, 'run', launch)
    native_trial(directory, data, point)
    command = commands[0]
    assert command[command.index('--cup-radius') + 1] == '0.2'
    assert command[command.index('--cup-depth') + 1] == '0.18'
    assert command[command.index('--image-age-periods') + 1] == '2.0'
    assert json.loads((directory / 'benchmark.json').read_text())['memory'] is None


def test_percentiles_use_request_counts():
    assert percentile({40: 95, 1000: 5}, .95) == 40
    assert percentile({40: 95, 1000: 5}, .99) == 1000
    assert percentile({}, .99) is None


def write_memory(tmp_path, samples=100):
    lines = ['---------- Begin Simulation Statistics ----------',
             'simSeconds 0.000001', 'simFreq 1000000000000',
             f'system.memory.cpuReads {samples}',
             'system.memory.cpuReadLatencyTicks 8800000',
             'system.memory.cpuReadTotalLatencyTicks 17050000',
             'system.memory.cpuRetries 20', 'system.memory.aggressorBytes 128',
             'system.memory.aggressorAccepted 2', 'system.memory.aggressorRejected 3',
             'system.memory.aggressorStallCycles 3']
    if samples:
        lines += ['system.memory.cpuReadLatencyNs::40 95',
                  'system.memory.cpuReadLatencyNs::1000 5',
                  'system.memory.cpuReadTotalLatencyNs::50 94',
                  'system.memory.cpuReadTotalLatencyNs::70 5',
                  'system.memory.cpuReadTotalLatencyNs::12000 1']
    (tmp_path / 'stats.txt').write_text('\n'.join(lines))
    (tmp_path / 'system.memory.ramulator_stats.yaml').write_text(
        'memory_system:\n  controllers:\n    - num_read_reqs_served: 9\n      num_write_reqs_served: 1\n')


def test_memory_units_tail_and_physical_bus(tmp_path):
    write_memory(tmp_path)
    result = memory_metrics(tmp_path, 51.2)
    assert result['accepted_mean_ns'] == pytest.approx(88)
    assert result['total_mean_ns'] == pytest.approx(170.5)
    assert result['accepted_p95_ns'] == 40
    assert result['accepted_p99_ns'] == 1000
    assert result['total_p99_ns'] == 70
    assert result['dram_bus_gbps'] == pytest.approx(.64)
    assert result['aggressor_accepted_gbps'] == pytest.approx(.128)
    assert result['guest_retries'] == 20


def test_missing_histogram_rejects_old_binary(tmp_path):
    write_memory(tmp_path, samples=99)
    with pytest.raises(ValueError, match='sample count mismatch'):
        memory_metrics(tmp_path, 51.2)


def test_no_guest_reads_are_unavailable_not_zero_latency(tmp_path):
    write_memory(tmp_path, samples=0)
    result = memory_metrics(tmp_path, 51.2)
    assert result['accepted_mean_ns'] is None
    assert result['total_p99_ns'] is None


def test_delivered_cadence_uses_host_sim_time(tmp_path):
    path = tmp_path / 'commands.csv'
    path.write_text('receipt_time,command_time\n0,0\n1,1\n1,1\n1.004,1\n1.012,1\n4,4\n')
    result = command_metrics(path, 1, 3, 250)
    assert result['delivered_commands'] == 3
    assert result['delivered_control_hz'] == pytest.approx(2 / .012)
    assert result['command_late_fraction'] == .5


def test_profiles_change_target_rates_in_report(tmp_path):
    from test_experiment_report import write_trial
    from report import load_trial
    write_trial(tmp_path)
    path = tmp_path / 'experiment.json'
    config = json.loads(path.read_text())
    config.update(planner_hz=60, plan_interval=.02, control_hz=500)
    path.write_text(json.dumps(config))
    details = load_trial(tmp_path)['summary']['timing_detail']
    assert next(row for row in details if row['stage'] == 'planning')['desired_hz'] == 50
    assert next(row for row in details if row['stage'] == 'control')['desired_hz'] == 500


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required for offline dashboard controls')
def test_cpu_comparison_preserves_duplicate_trial_names_and_per_model_baselines(tmp_path):
    from test_experiment_report import write_trial
    from report import cpu_comparison_suites, create_dashboard
    paths = []
    for index, cpu_type in enumerate(('timing', 'o3')):
        suite = tmp_path / cpu_type
        directory = suite / 'case-003-attempt-001'
        directory.mkdir(parents=True)
        write_trial(directory, success=bool(index))
        data = load_config(WORKSPACE / 'experiments/memory_contention.json', cpu_type)
        point = plan(data)[3]
        point.update(status='complete', trials=[directory.name])
        (suite / 'experiment_manifest.json').write_text(json.dumps(dict(
            mode='gem5', configuration=data, points=[point])))
        memory = {f'{scope}_{metric}_ns': 100 * (index + 1)
                  for scope in ('accepted', 'total') for metric in ('mean', 'p95', 'p99')}
        memory.update(aggressor_accepted_gbps=0, dram_bus_peak_fraction=.05)
        (directory / 'benchmark.json').write_text(json.dumps(dict(memory=memory, commands={})))
        paths.append(suite)
    directories, points, identifiers = cpu_comparison_suites(paths)
    dashboard = create_dashboard(directories, tmp_path / 'comparison.html', points, identifiers)
    subprocess.run(['node', str(WORKSPACE / 'tests/memory_dashboard_smoke.cjs'), str(dashboard),
                    'cpu-comparison'], check=True)
    assert [point['cpu_type'] for point in points] == ['timing', 'o3']
    manifest_path = paths[1] / 'experiment_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['configuration']['fixed']['camera_width'] = 320
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='must match except'):
        cpu_comparison_suites(paths)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required for offline dashboard controls')
def test_comparison_controls_preserve_outcomes_and_missing_measurements(tmp_path):
    from test_experiment_report import write_trial
    from report import create_dashboard
    points = []
    trials = []
    for index in range(4):
        point = dict(id=f'case-{index}', label=f'Case {index}',
                     throw={'name': 'other' if index == 2 else 'loft'},
                     rates=dict(name='reduced' if index == 3 else 'standard',
                                camera_hz=90, planner_hz=30, control_hz=250),
                     pressure=0 if index != 1 else 1.25, expected_throws=1,
                     status='infrastructure_failure' if index == 3 else 'complete', trials=[])
        if index < 3:
            directory = tmp_path / f'trial-{index}'
            directory.mkdir()
            write_trial(directory, success=index != 1)
            memory = {f'{scope}_{metric}_ns': (100 if index == 0 else 200)
                      for scope in ('accepted', 'total') for metric in ('mean', 'p95', 'p99')}
            memory.update(aggressor_accepted_gbps=index * 20, dram_bus_peak_fraction=.4)
            (directory / 'benchmark.json').write_text(json.dumps(dict(
                memory=memory if index < 2 else None, commands={'delivered_control_hz': 240})))
            trials.append(directory)
            point['trials'] = [directory.name]
        points.append(point)
    report = create_dashboard(trials, tmp_path / 'dashboard.html', points)
    subprocess.run(['node', str(WORKSPACE / 'tests/memory_dashboard_smoke.cjs'), str(report)], check=True)
