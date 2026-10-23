import json
from unittest.mock import patch

import pytest

from removal import remove, validate_cleanup
from test_scheduler import controller, experiment, finish_jobs


def request(controller, cleanup=None):
    key = controller.command('remove_machine', dict(id='m0', cleanup=cleanup or {}))
    controller.apply(controller.store.get('commands', key))


def test_unregister_offline_keeps_history_and_needs_no_ssh(controller):
    value = experiment(controller)
    value.update(status='completed', remote_cleaned=True)
    controller.save(value)
    request(controller)
    controller.tick()
    finish_jobs(controller)
    assert controller.store.get('machines', 'm0') is None
    assert controller.experiment('e1')['machines'] == ['m0', 'm1', 'm2']
    assert not any(m == 'm0' for m, _, _ in controller.remote.calls)


@pytest.mark.parametrize('status,cleaned', [('queued', True), ('running', True), ('stopped', False), ('completed', False)])
def test_unfinished_experiments_prevent_removal(controller, status, cleaned):
    value = experiment(controller)
    value.update(status=status, remote_cleaned=cleaned)
    controller.save(value)
    with pytest.raises(ValueError, match='Finalize'):
        request(controller)
    assert not controller.machine('m0').get('removing')


def test_failed_cleanup_keeps_machine_for_retry(controller):
    request(controller, {'supervisor': True})
    controller.failed('remove_machine', 'm0', 'SSH unavailable')
    assert controller.machine('m0')['removal_error'] == 'SSH unavailable'
    assert not controller.machine('m0')['removing']
    request(controller)
    assert controller.machine('m0')['removing']


def test_removing_machine_cannot_be_edited_or_deployed(controller):
    request(controller)
    with pytest.raises(ValueError, match='removal'):
        controller.apply(dict(kind='deploy', body={'id': 'm0'}))
    with pytest.raises(ValueError, match='removal'):
        controller.apply(dict(kind='machine', body=controller.machine('m0')))


@pytest.mark.parametrize('options', [[], {'unknown': True}, {'supervisor': 1}, {'images': True}])
def test_cleanup_options_are_strict(options):
    with pytest.raises(ValueError):
        validate_cleanup(options)


def test_remote_cleanup_preserves_configured_and_mounted_assets(tmp_path):
    assets = tmp_path / '.chimaera-orchestrator/assets'
    assets.mkdir(parents=True)
    configured = assets / 'ball-catching-configured.img'
    mounted = assets / 'ball-catching-mounted.img'
    unused = assets / 'ball-catching-unused.img'
    for path in (configured, mounted, unused):
        path.write_text('guest')
    outside = tmp_path / 'external'
    outside.mkdir()
    (assets / ('kernel-' + 'a' * 64)).symlink_to(outside)
    containers = [dict(Id='external', Mounts=[dict(Source=str(mounted))])]
    def docker(argv, **kwargs):
        return 'external' if argv[:3] == ['docker', 'ps', '-aq'] else json.dumps(containers)
    with patch('removal.run', side_effect=docker):
        remove(dict(repo=str(tmp_path), disk=str(configured)), {'supervisor': True, 'guest_assets': True})
    assert configured.exists() and mounted.exists() and outside.exists()
    assert not unused.exists()


def test_remote_cleanup_rejects_symlink_state_directory(tmp_path):
    (tmp_path / '.chimaera-orchestrator').symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='symlink'):
        remove(dict(repo=str(tmp_path)), {'supervisor': True})


def test_remote_cleanup_refuses_running_workers_before_deletion(tmp_path):
    worker = dict(Id='busy', Config={'Labels': {'chimaera.experiment': 'e1'}}, State={'Running': True})
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        return 'busy' if argv[:3] == ['docker', 'ps', '-aq'] else json.dumps([worker])
    with patch('removal.run', side_effect=docker), pytest.raises(ValueError, match='Stop all'):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'experiment_data': True})
    assert not any('rm' in call for call in calls)


def test_remote_cleanup_cancels_accepted_tasks_without_deleting_release_markers(tmp_path):
    from common import Store
    data = tmp_path / '.chimaera-orchestrator'
    store = Store(data / 'agent.sqlite')
    store.put('tasks', 'pending', dict(id='pending', experiment='e1', status='accepted'))
    store.put('released', 'older', True)
    store.put('lifecycle', 'active', 'e1')
    with patch('removal.run', return_value=''):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'experiment_data': True})
    assert store.all('tasks') == []
    assert store.get('released', 'e1') is True
    assert store.get('released', 'older') is True
    assert store.get('lifecycle', 'active') is None


def test_cleanup_failure_propagates_and_never_forces_image_removal(tmp_path):
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ['docker', 'ps', '-aq']:
            return ''
        if argv[:3] == ['docker', 'image', 'ls']:
            return json.dumps(dict(Repository='chimaera', Tag='orchestrator-app'))
        raise RuntimeError('Image is being used by another container')
    with patch('removal.run', side_effect=docker), pytest.raises(RuntimeError, match='being used'):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'images': True})
    assert ['docker', 'image', 'rm', 'chimaera:orchestrator-app'] in calls
    assert all('-f' not in argv and 'prune' not in argv for argv in calls)


def test_experiment_cleanup_preserves_configured_assets_inside_working_files(tmp_path):
    disk = tmp_path / '.chimaera-orchestrator/experiments/e1/guest.img'
    disk.parent.mkdir(parents=True)
    disk.write_text('configured guest')
    with patch('removal.run', return_value=''), pytest.raises(ValueError, match='configured asset'):
        remove(dict(repo=str(tmp_path), disk=str(disk)), {'supervisor': True, 'experiment_data': True})
    assert disk.read_text() == 'configured guest'


def test_selected_cleanup_completes_before_unregistering(controller):
    calls = []
    controller.remote.remove = lambda machine, cleanup: calls.append((machine['id'], cleanup)) or True
    request(controller, {'supervisor': True, 'images': True})
    assert controller.machine('m0')['removing']
    controller.tick()
    finish_jobs(controller)
    assert calls == [('m0', {'supervisor': True, 'images': True})]
    assert controller.store.get('machines', 'm0') is None
