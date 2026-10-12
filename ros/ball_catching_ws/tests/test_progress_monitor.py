import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from watch_memory_suite import display, snapshot


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
