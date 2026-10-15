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
