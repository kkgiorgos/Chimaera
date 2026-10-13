import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from watch_memory_suite import display, snapshot


def test_readiness_and_image_stages_expose_the_blocking_component(tmp_path):
    attempt = tmp_path / 'case-003-attempt-001'
    (attempt / 'gem5').mkdir(parents=True)
    (attempt / 'timing.csv').write_text('step,sim_seconds,elapsed_wall_seconds\n1,0.1,50\n')
    (attempt / 'readiness.csv').write_text(
        'time,arm_ready,perception_ready,warmup_elapsed,warmup_required\n'
        '0.1,1,0,0,0.1\n0.15,1,')
    (attempt / 'gem5/board.pc.com_1.device').write_text(
        'CHIMAERA_IMAGE component=bridge stage=copied capture_ns=1000000 now_ns=11000000 age_ns=10000000 budget_ns=22000000\n'
        '[perception-1] CHIMAERA_IMAGE component=perception stage=detected capture_ns=1000000 now_ns=41000000 age_ns=40000000 budget_ns=22000000\r\n'
        'CHIMAERA_IMAGE component=perception stage=detected capture_ns=1 now_ns=4 age_ns=9 budget_ns=22\n'
        'CHIMAERA_IMAGE component=perception stage=input capture_ns=')
    state = snapshot(tmp_path)
    assert state['readiness'] == dict(arm=True, perception=False, warmup_elapsed=0, warmup_required=.1)
    assert state['image_stages']['bridge.copied']['age_ms'] == 10
    assert state['image_stages']['perception.detected']['age_ms'] == 40
    assert 'fresh camera ready: no' in display(state)
    assert 'detected=40.0 ms' in display(state)
    assert 'outcome' not in state


def test_prepared_environment_does_not_report_a_throw_or_result(tmp_path):
    state = snapshot(tmp_path)
    assert state['phase'] == 'Waiting for a trial directory'
    assert 'outcome' not in state and 'throw_percent' not in state


def test_partial_live_records_are_ignored_until_complete(tmp_path):
    attempt = tmp_path / 'case-003-attempt-001'
    attempt.mkdir()
    (attempt / 'timing.csv').write_text(
        'step,sim_seconds,elapsed_wall_seconds\n1,0.0005,2\n2,0.0005,')
    (attempt / 'launch.json').write_text('{"launch_time":')
    (attempt / 'result.json').write_text('{"success":')
    (attempt / 'commands.csv').write_text('receipt_time,command_time\n0.0005,0\n')
    state = snapshot(tmp_path)
    assert state['completed_exchanges'] == 1
    assert state['simulated_seconds'] == .0005
    assert state['average_speed'] == .00025
    assert state['last_command_age_ms'] == .5
    assert 'outcome' not in state and 'throw_percent' not in state


def test_task_percentage_starts_at_launch_and_scoring_preserves_a_miss(tmp_path):
    attempt = tmp_path / 'case-006-attempt-001'
    attempt.mkdir()
    (tmp_path / 'experiment_manifest.json').write_text(json.dumps(dict(
        configuration=dict(hardware=dict(cpu_type='o3')),
        points=[dict(id='case-006', status='running')])))
    (attempt / 'execution.json').write_text('{"fixed":{"duration":2}}')
    (attempt / 'timing.csv').write_text('step,sim_seconds,elapsed_wall_seconds\n1,1.5,50\n')
    (attempt / 'launch.json').write_text('{"launch_time":1}')
    state = snapshot(attempt)
    assert state['case'] == 'case-006' and state['cpu_type'] == 'o3'
    assert state['phase'] == 'Ball launched'
    assert state['throw_percent'] == 25
    assert 'outcome' not in state
    (attempt / 'result.json').write_text('{"success":false}')
    state = snapshot(attempt)
    assert state['phase'] == 'Task scored' and state['outcome'] == 'MISS'
    assert 'Result: MISS' in display(state)


def test_guest_failure_is_visible_even_while_host_time_keeps_advancing(tmp_path):
    attempt = tmp_path / 'case-003-attempt-001'
    (attempt / 'gem5').mkdir(parents=True)
    (tmp_path / 'experiment_manifest.json').write_text(json.dumps(dict(
        configuration=dict(hardware=dict(cpu_type='o3')),
        points=[dict(id='case-003', status='running')])))
    (attempt / 'timing.csv').write_text('step,sim_seconds,elapsed_wall_seconds\n1,0.5,50\n')
    serial = attempt / 'gem5/board.pc.com_1.device'
    serial.write_text('warning: kernel recovered from I/O error\n')
    assert snapshot(tmp_path)['status'] == 'running'
    serial.write_text('Guest bridge: trailing controller data\r\n'
                      'guest_bridge exited (1); stopping session\r\n')
    state = snapshot(tmp_path)
    assert state['status'] == 'infrastructure_failure'
    assert state['phase'] == 'Guest application failed'
    assert state['reason'] == 'Guest bridge: trailing controller data'
    assert state['host_running_after_guest_failure']
    assert 'outcome' not in state
    assert 'guest work has failed' in display(state)


def test_boot_panic_replaces_an_uninformative_saved_reason(tmp_path):
    attempt = tmp_path / 'case-003-attempt-001'
    attempt.mkdir()
    (tmp_path / 'experiment_manifest.json').write_text(json.dumps(dict(
        points=[dict(id='case-003', status='infrastructure_failure',
                     reason='Co-simulation exited without task result; inspect logs')])))
    failure = '[gem5.opt-5] src/chimaera/util.cc:47: panic: connect: Connection refused'
    (attempt / 'launch.log').write_text(failure + '\n'
        + '[gazebo_host_bridge-4] Host bridge: startup cancelled\n')
    state = snapshot(tmp_path)
    assert state['phase'] == 'Co-simulation component failed'
    assert state['reason'] == failure
    assert 'outcome' not in state


def test_invalid_launch_does_not_display_a_raw_scorer_miss(tmp_path):
    attempt = tmp_path / 'case-003-attempt-001'
    attempt.mkdir()
    (tmp_path / 'experiment_manifest.json').write_text(json.dumps(dict(
        points=[dict(id='case-003', status='complete')])))
    (attempt / 'result.json').write_text('{"success":false}')
    (attempt / 'launch_validation.json').write_text(
        '{"valid":false,"reason":"launch_pose_or_velocity_reset"}')
    state = snapshot(tmp_path)
    assert state['status'] == 'infrastructure_failure'
    assert state['phase'] == 'Ball launch failed'
    assert 'outcome' not in state
    assert 'Result: MISS' not in display(state)
