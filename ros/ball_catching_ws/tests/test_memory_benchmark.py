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
    ('aggressor', 'backpressure', False), ('aggressor', 'stride', 1),
    ('aggressor', 'duty_percent', 50), ('hardware', 'num_cores', 0)])
def test_invalid_experiments_fail_before_launch(tmp_path, section, key, value):
    raw = configuration(tmp_path)
    raw[section][key] = value
    if key == 'camera_width':
        raw['fixed']['camera_height'] = 4096
    with pytest.raises(ValueError):
        load(tmp_path, raw)


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
