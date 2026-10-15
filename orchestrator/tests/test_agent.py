import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import Agent


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
