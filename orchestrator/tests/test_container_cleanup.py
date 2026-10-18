import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from agent import Agent


def task_record(agent, kind='case', status='running'):
    output = agent.directory('e') / ('attempts/a' if kind == 'case' else '')
    output.mkdir(parents=True, exist_ok=True)
    task = dict(id='a', experiment='e', case='case-a', kind=kind, status=status,
                container='chimaera-a', output=str(output))
    agent.store.put('tasks', 'a', task)
    return task


def capture_logs(argv, **kwargs):
    assert argv[:2] == ['docker', 'logs']
    kwargs['stdout'].write(b'worker stdout\nworker stderr\n')


@pytest.mark.parametrize('kind', ['case', 'prepare'])
@pytest.mark.parametrize('exit_code', [0, 1])
def test_finished_container_removed_after_logs_and_status_are_saved(tmp_path, kind, exit_code):
    root = Path(__file__).resolve().parents[2]
    agent = Agent(root, tmp_path / 'data')
    task = task_record(agent, kind)
    output = Path(task['output'])
    if kind == 'case':
        (output / 'suite').mkdir()
        (output / 'suite/experiment_manifest.json').write_text(json.dumps(dict(
            points=[dict(id='case-a', status='complete', trials=['trial'])])))
    else:
        (output / 'ready.json').write_text('{"records":{"a":1},"provenance":{"a":1}}')

    def docker(argv, **kwargs):
        if argv[1] == 'inspect':
            return json.dumps([dict(State=dict(Running=False, ExitCode=exit_code))])
        assert argv == ['docker', 'rm', 'chimaera-a']
        saved = agent.store.get('tasks', 'a')
        assert saved['status'] == ('completed' if exit_code == 0 else 'failed')
        assert saved['worker_log_saved']
        assert (output / 'worker.log').read_text() == 'worker stdout\nworker stderr\n'
        return b''

    with patch('agent.run', side_effect=docker), patch('agent.subprocess.run', side_effect=capture_logs):
        agent.reconcile()
    assert agent.store.get('tasks', 'a')['container_removed']
    with patch('agent.subprocess.run') as docker_logs:
        assert agent.logs('a') == 'worker stdout\nworker stderr'
        docker_logs.assert_not_called()
    if kind == 'prepare':
        assert (output / 'preparation-report/worker.log').read_bytes() == (output / 'worker.log').read_bytes()
    reopened = Agent(root, tmp_path / 'data')
    with patch('agent.run') as docker:
        reopened.reconcile()
        docker.assert_not_called()


def test_cleanup_retries_after_restart_without_losing_result(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = task_record(agent, status='completed')
    with patch('agent.subprocess.run', side_effect=capture_logs), \
         patch('agent.run', side_effect=RuntimeError('Docker unavailable')):
        agent.reconcile()
    saved = agent.store.get('tasks', 'a')
    assert saved['status'] == 'completed'
    assert saved['container_cleanup_error'] == 'Docker unavailable'
    assert saved['worker_log_saved']
    reopened = Agent(tmp_path, tmp_path / 'data')
    with patch('agent.subprocess.run') as logs, patch('agent.run', return_value=b'') as docker:
        reopened.reconcile()
        logs.assert_not_called()
        docker.assert_called_once_with(['docker', 'rm', task['container']], timeout=30)
    assert reopened.store.get('tasks', 'a')['container_removed']
    assert 'container_cleanup_error' not in reopened.store.get('tasks', 'a')


def test_log_failure_keeps_container_and_previous_logs_for_retry(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = task_record(agent, status='failed')
    log = Path(task['output']) / 'worker.log'
    log.write_text('previous logs')
    with patch('agent.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['docker', 'logs'])), \
         patch('agent.run') as docker:
        agent.reconcile()
        docker.assert_not_called()
    assert log.read_text() == 'previous logs'
    assert not log.with_name('worker.log.tmp').exists()
    assert agent.store.get('tasks', 'a')['container_cleanup_error']
    with patch('agent.subprocess.run', side_effect=capture_logs), patch('agent.run', return_value=b''):
        agent.reconcile()
    assert agent.store.get('tasks', 'a')['container_removed']


def test_already_removed_container_is_successful_cleanup(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task = task_record(agent, status='stopped')
    task['worker_log_saved'] = True
    agent.store.put('tasks', 'a', task)
    with patch('agent.run', side_effect=RuntimeError('Error: No such container: chimaera-a')):
        agent.reconcile()
    assert agent.store.get('tasks', 'a')['container_removed']


def test_running_container_is_not_cleaned(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task_record(agent)
    with patch('agent.run', return_value='[{"State":{"Running":true}}]') as docker, \
         patch('agent.subprocess.run') as logs:
        agent.reconcile()
        docker.assert_called_once_with(['docker', 'inspect', 'chimaera-a'], text=True, timeout=10)
        logs.assert_not_called()


def test_inspect_failure_does_not_finalize_or_clean_running_task(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task_record(agent)
    with patch('agent.run', side_effect=RuntimeError('Docker unavailable')) as docker, \
         pytest.raises(RuntimeError, match='Docker unavailable'):
        agent.reconcile()
    assert agent.store.get('tasks', 'a')['status'] == 'running'
    docker.assert_called_once_with(['docker', 'inspect', 'chimaera-a'], text=True, timeout=10)


def test_stopping_experiment_removes_containers_and_keeps_logs(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task_record(agent)
    def docker(argv, **kwargs):
        if argv[1] == 'ps':
            return 'chimaera-a'
        if argv[1] == 'inspect':
            return '[{"State":{"Running":false}}]'
        assert argv == ['docker', 'rm', 'chimaera-a']
        return b''

    def command(argv, **kwargs):
        if argv[1] == 'logs':
            capture_logs(argv, **kwargs)
        else:
            assert argv == ['docker', 'kill', 'chimaera-a']

    with patch.object(agent, 'reconcile'), patch('agent.run', side_effect=docker), \
         patch('agent.subprocess.run', side_effect=command), \
         patch.object(agent, 'snapshot', side_effect=lambda: Agent.reconcile(agent)):
        agent.stop('e')
    saved = agent.store.get('tasks', 'a')
    assert saved['status'] == 'stopped'
    assert saved['container_removed']
    assert agent.logs('a') == 'worker stdout\nworker stderr'


def test_failed_experiment_stop_preserves_running_task(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    task_record(agent)
    with patch.object(agent, 'reconcile'), \
         patch('agent.run', side_effect=['chimaera-a', '[{"State":{"Running":true}}]']) as docker, \
         patch('agent.subprocess.run'), pytest.raises(RuntimeError, match='still running'):
        agent.stop('e')
    assert agent.store.get('tasks', 'a')['status'] == 'running'
    assert all(call.args[0][1] != 'rm' for call in docker.call_args_list)
