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


def local_controller(root):
    return dict(Id='controller', Name='chimaera-orchestrator-controller-1',
                Config={'Labels': {'com.docker.compose.service': 'controller',
                                  'com.docker.compose.project.working_dir': str(root / 'orchestrator')}},
                Mounts=[dict(Source=str(root), Destination='/repo', RW=False)])


def test_controller_host_cleanup_keeps_control_plane_and_collected_results(tmp_path):
    data = tmp_path / '.chimaera-orchestrator'
    experiment_files = data / 'experiments/e1'
    experiment_files.mkdir(parents=True)
    (experiment_files / 'worker.log').write_text('remote working log')
    assets = data / 'assets'
    assets.mkdir()
    guest = assets / 'ball-catching-generated.img'
    guest.write_text('generated guest')
    results = tmp_path / 'controller-volume/results/e1'
    results.mkdir(parents=True)
    (results / 'worker.log').write_text('collected result')
    control = local_controller(tmp_path)
    control['Mounts'].append(dict(Source=str(results.parent.parent), Destination='/data', RW=True))
    dashboard = dict(Id='dashboard', Name='chimaera-orchestrator-dashboard-1', Mounts=[])
    supervisor = dict(Id='supervisor', Name='chimaera-supervisor',
                      Mounts=[dict(Source=str(tmp_path), Destination=str(tmp_path))])
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ['docker', 'ps', '-aq']:
            return 'controller dashboard supervisor'
        if argv[:2] == ['docker', 'inspect']:
            return json.dumps([control, dashboard, supervisor])
        return ''
    with patch('removal.run', side_effect=docker):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'experiment_data': True,
                                         'guest_assets': True, 'images': True})
    assert not experiment_files.exists()
    assert not guest.exists()
    assert (results / 'worker.log').read_text() == 'collected result'
    assert [argv for argv in calls if argv[:2] == ['docker', 'rm']] == [['docker', 'rm', '-f', 'supervisor']]


@pytest.mark.parametrize('change', ['other_service', 'other_checkout', 'writable', 'direct_experiments'])
def test_controller_mount_exception_does_not_exempt_other_consumers(tmp_path, change):
    directory = tmp_path / '.chimaera-orchestrator/experiments/e1'
    directory.mkdir(parents=True)
    (directory / 'worker.log').write_text('keep')
    control = local_controller(tmp_path)
    labels = control['Config']['Labels']
    mount = control['Mounts'][0]
    if change == 'other_service':
        labels['com.docker.compose.service'] = 'analysis'
    elif change == 'other_checkout':
        labels['com.docker.compose.project.working_dir'] = '/different/orchestrator'
    elif change == 'writable':
        mount['RW'] = True
    else:
        mount['Source'] = str(directory)
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        return 'controller' if argv[:3] == ['docker', 'ps', '-aq'] else json.dumps([control])
    with patch('removal.run', side_effect=docker), pytest.raises(ValueError, match='mounted by another'):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'experiment_data': True})
    assert (directory / 'worker.log').read_text() == 'keep'
    assert not any(argv[:2] == ['docker', 'rm'] for argv in calls)


def test_controller_direct_asset_mount_stays_protected(tmp_path):
    assets = tmp_path / '.chimaera-orchestrator/assets'
    assets.mkdir(parents=True)
    mounted = assets / 'ball-catching-mounted.img'
    mounted.write_text('keep')
    unused = assets / 'ball-catching-unused.img'
    unused.write_text('delete')
    control = local_controller(tmp_path)
    control['Mounts'].append(dict(Source=str(mounted), Destination='/guest.img', RW=False))
    def docker(argv, **kwargs):
        return 'controller' if argv[:3] == ['docker', 'ps', '-aq'] else json.dumps([control])
    with patch('removal.run', side_effect=docker):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'guest_assets': True})
    assert mounted.exists()
    assert not unused.exists()


@pytest.mark.parametrize('read_only', [True, False])
def test_host_filesystem_browser_only_pins_files_when_writable(tmp_path, read_only):
    directory = tmp_path / '.chimaera-orchestrator/experiments/e1'
    directory.mkdir(parents=True)
    guest = tmp_path / '.chimaera-orchestrator/assets/ball-catching-generated.img'
    guest.parent.mkdir()
    guest.write_text('guest')
    viewer = dict(Id='viewer', Mounts=[dict(Source='/', Destination='/host', RW=not read_only)])
    def docker(argv, **kwargs):
        return 'viewer' if argv[:3] == ['docker', 'ps', '-aq'] else json.dumps([viewer])
    with patch('removal.run', side_effect=docker):
        if read_only:
            remove(dict(repo=str(tmp_path)), {'supervisor': True, 'experiment_data': True, 'guest_assets': True})
            assert not directory.exists() and not guest.exists()
        else:
            with pytest.raises(ValueError, match='mounted by another container: /'):
                remove(dict(repo=str(tmp_path)), {'supervisor': True, 'experiment_data': True, 'guest_assets': True})
            assert directory.exists() and guest.exists()


def test_podman_image_cleanup_handles_lowercase_fields_and_preserves_controller_images(tmp_path):
    images = [dict(repository='localhost/chimaera', tag='orchestrator-app'),
              dict(repository='localhost/chimaera-supervisor', tag='local'),
              dict(repository='localhost/chimaera-orchestrator-controller', tag='latest'),
              dict(repository='localhost/chimaera-orchestrator-dashboard', tag='latest'),
              dict(repository='registry.example/chimaera', tag='orchestrator-app')]
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ['docker', 'image', 'ls']:
            return '\n'.join(json.dumps(image) for image in images)
        return ''
    with patch('removal.run', side_effect=docker):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'images': True})
    assert [argv for argv in calls if argv[:3] == ['docker', 'image', 'rm']] == [
        ['docker', 'image', 'rm', 'localhost/chimaera-supervisor:local', 'localhost/chimaera:orchestrator-app']]


def test_podman_tracked_image_ids_without_sha_prefix_are_removed(tmp_path):
    assets = tmp_path / '.chimaera-orchestrator/assets'
    assets.mkdir(parents=True)
    image = 'sha256:' + 'a' * 64
    marker = assets / ('image-' + 'a' * 64 + '.json')
    marker.write_text(json.dumps(dict(image_id=image)))
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        return 'a' * 64 if argv == ['docker', 'image', 'ls', '-q', '--no-trunc'] else ''
    with patch('removal.run', side_effect=docker):
        remove(dict(repo=str(tmp_path)), {'supervisor': True, 'images': True})
    assert ['docker', 'image', 'rm', image] in calls
    assert not marker.exists()
