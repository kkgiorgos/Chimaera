import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from agent import Agent, memory_bytes


@pytest.mark.parametrize('consumer', [False, True])
@pytest.mark.parametrize('backend', ['m5', 'dma'])
def test_preparation_selects_checkpoints_and_producer_kvm(tmp_path, consumer, backend):
    agent = Agent(tmp_path, tmp_path / 'data')
    original_stat = Path.stat
    def stat(path, *args, **kwargs):
        if path == Path('/var/run/docker.sock'):
            return SimpleNamespace(st_gid=999)
        return original_stat(path, *args, **kwargs)
    with patch('agent.Path.stat', stat), patch.object(agent, 'launch', return_value={}) as launch:
        agent.prepare('experiment', {'fixed': {'transport_backend': backend}}, 1, '2g', 2, {},
                      calibration={'records': {}} if consumer else None)
    request = json.loads((agent.directory('experiment') / 'request.json').read_text())
    assert request['readiness_checkpoint'] is (backend == 'm5')
    command = launch.call_args.args[3]
    assert ('--device' in command) is not consumer


def test_stop_generation_blocks_late_dispatch(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    agent.store.put('stopped', 'experiment', 0)
    with pytest.raises(ValueError, match='stopped'):
        agent.start('experiment', 'case-000', 'attempt', 1, '2g', generation=0)
    agent.check_generation('experiment', 1)


def test_duplicate_assignment_returns_existing_task(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = dict(id='a', experiment='e', kind='case', status='running', container='chimaera-a')
    agent.store.put('tasks', 'a', task)
    with patch('agent.run') as docker:
        assert agent.start('e', 'case-000', 'a', 1, '2g') == task
        docker.assert_not_called()


def test_accepted_assignment_recovers_existing_container(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = dict(id='a', experiment='e', kind='case', status='accepted', container='chimaera-a', command=['docker', 'run'])
    agent.store.put('tasks', 'a', task)
    with patch('agent.run', return_value=json.dumps([dict(State=dict(Running=True))])) as docker:
        agent.ensure_started(task)
        assert docker.call_count == 1
    assert agent.store.get('tasks', 'a')['status'] == 'running'


def test_supervisor_reopens_durable_assignment(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    agent.store.put('tasks', 'a', dict(id='a', status='running'))
    reopened = Agent(tmp_path, tmp_path / 'data')
    assert reopened.store.get('tasks', 'a')['status'] == 'running'


def test_missing_container_error_is_case_insensitive(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = dict(id='a', experiment='e', kind='case', status='accepted', container='chimaera-a', command=['docker', 'run'])
    agent.store.put('tasks', 'a', task)
    with patch('agent.run', side_effect=[RuntimeError('error: no such object: chimaera-a'), b'container-id']):
        agent.ensure_started(task)
    assert agent.store.get('tasks', 'a')['status'] == 'running'


def test_failed_log_capture_does_not_block_task_failure_or_stop(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    task = dict(id='a', case='case-000', experiment='e', kind='case', status='running',
                container='chimaera-a', output=str(tmp_path / 'output'))
    agent.store.put('tasks', 'a', task)
    state = json.dumps([dict(State=dict(Running=False, ExitCode=1, OOMKilled=False))])
    with patch('agent.run', return_value=state), \
         patch('agent.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['docker', 'logs'])):
        agent.reconcile()
    assert agent.store.get('tasks', 'a')['status'] == 'failed'
    assert agent.store.get('tasks', 'a')['log_error']
    with patch('agent.run', return_value=''):
        result = agent.stop('e')
    assert result['tasks'][0]['status'] == 'failed'


def test_missing_preparation_output_does_not_break_supervisor_snapshot(tmp_path):
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    agent.store.put('tasks', 'prep', dict(id='prep', experiment='e', kind='prepare',
        status='failed', container='missing', output=str(tmp_path / 'missing/output')))
    assert agent.snapshot()['tasks'][0]['status'] == 'failed'
    assert (tmp_path / 'missing/output/preparation-report/failure.json').exists()


def test_pre_checkpoint_cli_failure_is_visible_in_dashboard_and_collected_report(tmp_path):
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    output = agent.directory('e')
    output.mkdir(parents=True)
    agent.store.put('tasks', 'e-prepare', dict(id='e-prepare', experiment='e', kind='prepare',
        status='running', container='prepare', output=str(output)))
    log = ('usage: run_memory_suite.py [-h]\n'
        'run_memory_suite.py: error: Output must be empty or new\n'
        'worker.py: error: Command returned non-zero exit status 2\n'
        'setup.py: error: Worker failed\n')
    state = json.dumps([dict(State=dict(Running=False, ExitCode=1, OOMKilled=False))])
    def save_log(task):
        (output / 'worker.log').write_text(log)
        task['worker_log_saved'] = True
    with patch('agent.run', return_value=state), patch.object(agent, 'save_worker_log', save_log):
        failed = agent.snapshot()['tasks'][0]
    expected = 'run_memory_suite.py: error: Output must be empty or new'
    assert failed['error'] == expected
    assert json.loads((output / 'preparation-report/failure.json').read_text())['error'] == expected
    # Recover the cause for old failures whose saved task has no error field.
    failed.pop('error')
    agent.store.put('tasks', failed['id'], failed)
    assert agent.snapshot()['tasks'][0]['error'] == expected


def test_readiness_progress_and_failure_diagnostics_survive_collection(tmp_path):
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    output = agent.directory('e')
    checkpoint = output / 'inputs/prepared/checkpoints' / ('a' * 64)
    checkpoint.mkdir(parents=True)
    (output / 'stage.json').write_text('{"phase":"Calibrating and creating readiness checkpoints"}')
    (checkpoint / 'execution.json').write_text('{}')
    (checkpoint / 'timing.csv').write_text('step,sim_seconds,elapsed_wall_seconds\n1,0.0005,2\n')
    (checkpoint / 'readiness.csv').write_text(
        'time,arm_ready,perception_ready,warmup_elapsed,warmup_required\n0.0005,0,1,0,0\n')
    task = dict(id='e-prepare', experiment='e', kind='prepare', status='running',
        container='prepare', output=str(output), started=100)
    agent.store.put('tasks', task['id'], task)
    with patch.object(agent, 'reconcile'), patch('agent.run', return_value=''), patch('agent.time.time', return_value=105):
        sampled = agent.snapshot()['tasks'][0]
    assert sampled['phase'] == 'Waiting for arm readiness'
    assert sampled['progress']['elapsed_seconds'] == 5
    assert sampled['progress']['simulated_seconds'] == .0005
    (checkpoint / 'launch.log').write_text('[gazebo_host_bridge-4] Host bridge: socket timeout\n')
    (checkpoint / 'cpt').mkdir()
    (checkpoint / 'cpt/ram').write_bytes(b'excluded checkpoint memory')
    state = json.dumps([dict(State=dict(Running=False, ExitCode=1, OOMKilled=False))])
    with patch('agent.run', return_value=state), patch('agent.subprocess.run'):
        failed = agent.snapshot()['tasks'][0]
    assert failed['status'] == 'failed' and failed['phase'] == 'Preparation failed'
    assert 'socket timeout' in failed['error']
    report = output / 'preparation-report'
    assert json.loads((report / 'failure.json').read_text())['error'] == failed['error']
    assert (report / 'checkpoints' / checkpoint.name / 'launch.log').exists()
    assert not list(report.rglob('ram'))


def test_retry_preparation_tolerates_already_removed_container(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    agent.store.put('tasks', 'e-prepare', dict(id='e-prepare', status='failed', container='removed'))
    with patch('agent.run', side_effect=RuntimeError('Error: No such container: removed')):
        agent.reset_prepare('e')
    assert agent.store.get('tasks', 'e-prepare') is None


@pytest.mark.parametrize('size,expected', [('2g', 2 * 1024**3), ('512M', 512 * 1024**2),
    ('2.25GiB', int(2.25 * 1024**3)), ('900MB', 900000000), ('128B', 128)])
def test_memory_sizes(size, expected):
    assert memory_bytes(size) == expected


def test_case_launch_uses_soft_memory_budget(tmp_path):
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    directory = agent.directory('e')
    (directory / 'inputs').mkdir(parents=True)
    (directory / 'ready.json').write_text('{}')
    (directory / 'inputs/runner.json').write_text(json.dumps(dict(
        image='test:local', command=['true'], jobs=[dict(name='case-000')],
        mounts=[], devices=[], cpus=1, memory='2g')))
    with patch('agent.run', side_effect=[RuntimeError('No such container'), b'created']):
        task = agent.start('e', 'case-000', 'a', 1, '2g')
    argv = task['command']
    assert argv[argv.index('--memory-reservation') + 1] == '2g'
    assert '--memory' not in argv
    assert '--memory-swap' not in argv
    assert task['memory_budget_bytes'] == 2 * 1024**3


def test_memory_warning_survives_lower_usage_restart_and_completion(tmp_path):
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    agent.store.put('tasks', 'a', dict(id='a', case='case-000', experiment='e', kind='case',
        status='running', container='chimaera-a', output=str(tmp_path / 'output'),
        memory_budget_bytes=2 * 1024**3))

    def snapshot(usage):
        with patch.object(agent, 'reconcile'), patch('agent.run', return_value=json.dumps(
                dict(Name='chimaera-a', MemUsage=usage + ' / 32GiB'))):
            return agent.snapshot()['tasks'][0]

    assert 'memory_warning' not in snapshot('1.5GiB')
    exceeded = snapshot('2.5GiB')
    assert exceeded['status'] == 'running'
    assert 'increase RAM' in exceeded['memory_warning']
    agent = Agent(root, tmp_path / 'data')
    lower = snapshot('1.5GiB')
    assert lower['memory_peak_bytes'] == int(2.5 * 1024**3)
    assert lower['memory_warning'] == exceeded['memory_warning']
    lower['status'] = 'completed'
    agent.store.put('tasks', 'a', lower)
    with patch.object(agent, 'reconcile'):
        assert agent.snapshot()['tasks'][0]['memory_warning'] == exceeded['memory_warning']


def test_individual_stop_kills_only_selected_container_and_keeps_logs(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    for key in ('a', 'b'):
        agent.store.put('tasks', key, dict(id=key, experiment='e', case='case-' + key,
            kind='case', status='running', container='chimaera-' + key,
            output=str(agent.directory('e') / 'attempts' / key)))
    with patch('agent.run', side_effect=[json.dumps([dict(State=dict(Running=True))]), b'',
            json.dumps([dict(State=dict(Running=False))]), b'']) as docker, \
         patch('agent.subprocess.run') as logs:
        stopped = agent.rpc('stop_case', experiment='e', case='case-a', attempt='a')
    assert stopped['status'] == 'stopped'
    assert (Path(stopped['output']) / 'worker.log').exists()
    assert docker.call_args_list[1].args[0] == ['docker', 'kill', 'chimaera-a']
    assert logs.call_args.args[0] == ['docker', 'logs', 'chimaera-a']
    assert agent.store.get('tasks', 'b')['status'] == 'running'
    assert agent.stop_case('e', 'case-a', 'a')['status'] == 'stopped'


def test_stop_before_dispatch_blocks_late_start_after_restart(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    assert agent.stop_case('e', 'case-a', 'a')['status'] == 'stopped'
    reopened = Agent(tmp_path, tmp_path / 'data')
    with patch('agent.run') as docker, pytest.raises(ValueError, match='Attempt was stopped'):
        reopened.start('e', 'case-a', 'a', 1, '2g')
    docker.assert_not_called()


def test_stop_rejects_attempt_from_another_case(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    agent.store.put('tasks', 'a', dict(id='a', experiment='e', case='case-a', kind='case'))
    with pytest.raises(ValueError, match='does not belong'):
        agent.stop_case('other', 'case-a', 'a')
    assert not agent.store.get('stopped_attempts', 'a')


def test_recovery_does_not_launch_accepted_attempt_with_pending_stop(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = dict(id='a', experiment='e', case='case-a', kind='case', status='accepted',
        container='chimaera-a', output=str(agent.directory('e') / 'attempts/a'))
    agent.store.put('tasks', 'a', task)
    agent.store.put('stopped_attempts', 'a', True)
    with patch('agent.run', side_effect=RuntimeError('No such container')) as docker, \
         patch('agent.subprocess.run'):
        agent.ensure_started(task)
    assert docker.call_count == 1
    assert agent.store.get('tasks', 'a')['status'] == 'stopped'


def test_stop_preserves_case_that_already_finished(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = dict(id='a', experiment='e', case='case-a', kind='case', status='running',
        container='chimaera-a', output=str(tmp_path / 'a'))
    agent.store.put('tasks', 'a', task)
    def finished():
        agent.store.put('tasks', 'a', dict(task, status='completed'))
    with patch('agent.run', return_value=json.dumps([dict(State=dict(Running=False, ExitCode=0))])) as docker, \
         patch.object(agent, 'reconcile', side_effect=finished):
        assert agent.stop_case('e', 'case-a', 'a')['status'] == 'completed'
    assert docker.call_count == 1


def test_failed_kill_keeps_attempt_running_for_stop_retry(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    agent.store.put('tasks', 'a', dict(id='a', experiment='e', case='case-a', kind='case',
        status='running', container='chimaera-a', output=str(tmp_path / 'a')))
    running = json.dumps([dict(State=dict(Running=True))])
    with patch('agent.run', side_effect=[running, RuntimeError('kill failed'), running]), \
         pytest.raises(RuntimeError, match='kill failed'):
        agent.stop_case('e', 'case-a', 'a')
    assert agent.store.get('tasks', 'a')['status'] == 'running'
    assert agent.store.get('stopped_attempts', 'a')
