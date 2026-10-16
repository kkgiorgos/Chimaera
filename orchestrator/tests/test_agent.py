import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import Agent, memory_bytes


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
