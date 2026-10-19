import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from agent import Agent
from assets import clean_unused
from common import atomic_json, digest
from images import APPLICATION, BASE, SDK, build
from readiness_transfer import transfer
from test_scheduler import controller, experiment, finish_jobs


@pytest.mark.parametrize('status,cleaned', [('running', False), ('stopped', False),
    ('needs_attention', False), ('completed', False), ('partial', False)])
def test_an_unreleased_experiment_blocks_another_creation(controller, status, cleaned):
    previous = experiment(controller)
    previous.update(status=status, remote_cleaned=cleaned)
    controller.save(previous)
    with pytest.raises(ValueError, match='existing experiment'):
        controller.apply(dict(id='new', kind='experiment', body={}))
    assert len(controller.store.all('experiments')) == 1


@pytest.mark.parametrize('interrupted', [False, True])
def test_consumer_preparation_waits_for_separate_readiness_transfer(controller, interrupted):
    value = experiment(controller, count=1)
    app = 'sha256:' + 'b' * 64
    value['calibration']['provenance'].update(application_image_id=app,
        readiness_checkpoint=True, checkpoint_index_sha256='frozen checkpoint index')
    value['preparations']['m1'] = dict(status='pending')
    transfers = []

    def readiness(source, destination, experiment_id, expected, cancel=None):
        transfers.append((source['id'], destination['id'], experiment_id, expected))
        if interrupted:
            raise RuntimeError('Sparse transfer interrupted')

    controller.remote.copy_readiness = readiness
    controller.schedule(value)
    finish_jobs(controller)
    preparation = controller.experiment('e1')['preparations']['m1']
    assert transfers == [('m0', 'm1', 'e1', 'frozen checkpoint index')]
    copies = [c for c in controller.remote.calls if c[1] == 'copy_image']
    assert [c[2]['image'] for c in copies] == [app]
    assert preparation['status'] == ('failed' if interrupted else 'pending')
    assert not any(c[:2] == ('m1', 'prepare') for c in controller.remote.calls)


def completed_experiment(controller):
    value = experiment(controller, count=1)
    value.update(status='completed')
    value['cases'][0].update(status='completed', attempts=[dict(id='attempt', machine='m0',
        status='completed', collected=True)])
    for preparation in value['preparations'].values():
        preparation['collected'] = True
    controller.save(value)
    return value


def test_final_cleanup_waits_for_every_verified_copy(controller):
    value = completed_experiment(controller)
    value['preparations']['m2'].update(collected=False, collection_status='failed')
    controller.save(value)
    controller.reclaim(value)
    finish_jobs(controller)
    assert not any(c[1] == 'release' for c in controller.remote.calls)
    assert any(c[1] == 'release_attempt' for c in controller.remote.calls)
    value = controller.experiment('e1')
    value['preparations']['m2']['collected'] = True
    controller.reclaim(value)
    finish_jobs(controller)
    assert controller.experiment('e1')['remote_cleaned']
    assert {c[0] for c in controller.remote.calls if c[1] == 'release'} == {'m0', 'm1', 'm2'}


def test_release_retries_after_restart_and_keeps_central_results(controller):
    from controller import Controller
    value = completed_experiment(controller)
    result = controller.data / 'results/e1/result.json'
    atomic_json(result, dict(caught=True))
    controller.finished('release', ('e1', 'm0'), {})
    controller.failed('release', ('e1', 'm1'), 'Disconnected')
    reopened = Controller(controller.root, controller.data, controller.remote)
    try:
        reopened.reclaim(reopened.experiment('e1'))
        finish_jobs(reopened)
        saved = reopened.experiment('e1')
        assert saved['remote_cleaned']
        assert not saved['cleanup_errors']
        assert json.loads(result.read_text()) == dict(caught=True)
        assert not any(c[:2] == ('m0', 'release') for c in controller.remote.calls)
        # An old poll cannot recreate the already released preparation.
        before = saved['preparations'].copy()
        reopened.ingest(reopened.machine('m0'), dict(tasks=[dict(id='old', experiment='e1',
            kind='prepare', status='running', output='/old')]))
        assert reopened.experiment('e1')['preparations'] == before
    finally:
        reopened.close()


def test_finalizing_stopped_experiment_preserves_uncollected_output(controller):
    value = completed_experiment(controller)
    value['status'] = 'stopped'
    value['cases'][0]['attempts'][0].update(collected=False, collection_status='failed')
    controller.save(value)
    controller.apply(dict(kind='accept_partial', body=dict(id='e1')))
    controller.reclaim(controller.experiment('e1'))
    finish_jobs(controller)
    assert controller.experiment('e1')['status'] == 'partial'
    assert not any(c[1] == 'release' for c in controller.remote.calls)


def test_deleting_an_unstarted_experiment_does_not_wait_for_unused_offline_machines(controller):
    value = experiment(controller, count=1)
    value.update(status='deleted', results_deleted=True, preparations={})
    controller.save(value)
    controller.reclaim(value)
    assert controller.experiment('e1')['remote_cleaned']
    assert not any(c[1] == 'release' for c in controller.remote.calls)


def test_release_tombstone_rejects_late_rpc_after_supervisor_restart(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    directory = agent.directory('old')
    directory.mkdir(parents=True)
    (directory / 'disk.img').write_bytes(b'experiment disk')
    agent.claim('old')
    with patch('agent.run', return_value=''), patch('assets.clean_unused', return_value={}), \
         patch('images.prune'):
        agent.release('old')
    assert not directory.exists()
    reopened = Agent(tmp_path, tmp_path / 'data')
    with pytest.raises(ValueError, match='released'):
        reopened.asset_destination('old')
    reopened.claim('new')
    with pytest.raises(ValueError, match='existing experiment'):
        reopened.claim('another')


def test_failed_container_removal_keeps_the_worker_output(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    directory = agent.directory('e') / 'attempts/a'
    directory.mkdir(parents=True)
    result = directory / 'result.json'
    result.write_text('only remaining copy')
    agent.store.put('tasks', 'a', dict(id='a', kind='case', experiment='e', status='completed',
        container='chimaera-a', output=str(directory), worker_log_saved=True))
    with patch('agent.run', side_effect=RuntimeError('Docker offline')):
        with pytest.raises(ValueError, match='container has not been removed'):
            agent.release_attempt('e', 'a')
    assert result.exists()
    with patch('agent.run', return_value=''):
        agent.release_attempt('e', 'a')
        agent.release_attempt('e', 'a')
    assert not directory.exists()


def test_corrupt_incoming_assets_do_not_replace_an_existing_import(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    incoming = Path(agent.asset_destination('e'))
    atomic_json(incoming / 'checkpoints/index.json', {})
    destination = agent.directory('e') / 'shared-assets'
    destination.mkdir()
    (destination / 'previous').write_text('verified')
    with pytest.raises(ValueError, match='different checkpoint index'):
        agent.commit_assets('e', 'wrong digest')
    assert (destination / 'previous').read_text() == 'verified'


def test_retry_keeps_small_diagnostics_and_discards_old_checkpoint_ram(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    directory = agent.directory('e')
    atomic_json(directory / 'request.json', {})
    atomic_json(directory / 'preparation-report/failure.json', dict(error='failed'))
    ram = directory / 'inputs/prepared/checkpoints/ram'
    ram.parent.mkdir(parents=True)
    ram.write_bytes(b'large obsolete memory')
    agent.reset_prepare('e')
    assert not ram.exists()
    assert list(directory.glob('preparation-failures/*/preparation-report/failure.json'))


def test_image_slots_rebuild_only_the_application_when_its_sources_change(tmp_path):
    dockerfile = tmp_path / 'containers/ball-catching/Dockerfile'
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text('base dependencies and application stages')
    (tmp_path / 'containers/Dockerfile').write_text('FROM ubuntu AS image\n')
    (tmp_path / 'containers/Dockerfile.dockerignore').write_text('**\n')
    atomic_json(tmp_path / 'containers/profiles/jammy-humble-fortress.json',
                dict(platform='linux/amd64', base_image='ubuntu', ros_distro='humble'))
    tags = {}
    commands = []

    def docker(argv, **kwargs):
        commands.append(argv)
        if argv[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in tags else 1,
                                               stdout=tags.get(argv[-1], ''))
        if '--tag' in argv or '-t' in argv:
            flag = '--tag' if '--tag' in argv else '-t'
            tags[argv[argv.index(flag) + 1]] = 'sha256:' + str(len(commands)).zfill(64)
        if argv[:2] == ['docker', 'commit']:
            tags[argv[-1]] = 'sha256:' + str(len(commands)).zfill(64)
        return subprocess.CompletedProcess(argv, 0, stdout='')

    with patch('images.subprocess.run', side_effect=docker):
        first = build(tmp_path, dict(sdk='simulator', application='old app'), 2)
        commands.clear()
        assert build(tmp_path, dict(sdk='simulator', application='old app'), 4) == first
        assert not any('--load' in c or '--tag' in c for c in commands)
        commands.clear()
        updated = build(tmp_path, dict(sdk='simulator', application='new app'), 4)
    builds = [c for c in commands if '--load' in c or '--tag' in c or c[:2] == ['docker', 'commit']]
    assert len(builds) == 1
    assert builds[0][-1] == APPLICATION
    assert 'ENTRYPOINT ["/bin/bash", "/opt/chimaera/scripts/entrypoint.sh"]' in builds[0]
    assert first[:2] == updated[:2]
    cache = json.loads((tmp_path / '.chimaera-orchestrator/assets/current-images.json').read_text())
    assert set(cache) == {SDK, BASE, APPLICATION}
    assert cache[APPLICATION]['id'] == updated[2]
    prunes = [c for c in commands if c[:3] == ['docker', 'buildx', 'prune']]
    assert not prunes


def test_cleanup_retains_only_current_reusable_image_and_guest(tmp_path):
    current, obsolete = ['sha256:' + c * 64 for c in 'ab']
    disk = tmp_path / 'assets/ball-catching-current.img'
    disk.parent.mkdir()
    disk.write_bytes(b'cached guest')
    old = disk.with_name('ball-catching-old.img')
    old.write_bytes(b'obsolete guest')
    atomic_json(disk.parent / 'current-images.json', {APPLICATION: dict(id=current, key='current')})
    atomic_json(disk.parent / 'current-guest.json', dict(disk=str(disk)))
    listing = '\n'.join(json.dumps(dict(Repository='chimaera', Tag=tag, ID=image))
                        for tag, image in [('orchestrator-app', current), ('ball-old', obsolete)])
    with patch('assets.run', side_effect=['', listing, '']):
        removed = clean_unused(tmp_path)
    assert removed['images'] == removed['disks'] == 1
    assert disk.exists() and not old.exists()


def test_cleanup_retains_ancestors_needed_by_external_containers(tmp_path):
    image = 'sha256:' + 'a' * 64
    listing = json.dumps(dict(Repository='chimaera', Tag='ball-old', ID=image))
    marker = tmp_path / 'assets' / ('image-' + 'a' * 64 + '.json')
    atomic_json(marker, dict(image_id=image))
    with patch('assets.run', side_effect=['', listing,
               RuntimeError('conflict: image has dependent child images')]):
        assert clean_unused(tmp_path)['images'] == 0
    assert marker.exists()


def test_real_sparse_transfer_streams_without_an_archive_and_preserves_bytes(tmp_path):
    import shlex
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.mkdir()
    destination.mkdir()
    disk = source / 'guest.img'
    with disk.open('wb') as stream:
        stream.write(b'header')
        stream.seek(32 * 1024 * 1024)
        stream.write(b'tail')
    real_popen = subprocess.Popen
    processes = []

    def local_peer(argv, **kwargs):
        if argv[0] == 'fake-ssh':
            argv = shlex.split(argv[-1])[4:]
            assert argv[0] == 'tar'
        process = real_popen(argv, **kwargs)
        processes.append(process)
        return process

    reader, writer = os.pipe()
    try:
        with os.fdopen(reader, 'rb') as connection, patch('readiness_transfer.sys.stdin', connection), \
             patch('readiness_transfer.subprocess.Popen', side_effect=local_peer):
            transfer(str(source), str(destination), ['fake-ssh'])
    finally:
        os.close(writer)
    imported = destination / 'guest.img'
    assert digest(imported) == digest(disk)
    assert imported.stat().st_blocks * 512 < 1024 * 1024
    assert list(destination.iterdir()) == [imported]
    assert all(p.poll() == 0 for p in processes)


def test_readiness_transfer_disconnect_reaps_both_processes(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file').write_text('asset')
    real_popen = subprocess.Popen
    processes = []

    def hanging_peer(argv, **kwargs):
        if argv[0] == 'fake-ssh':
            argv = ['python3', '-c', 'import time; time.sleep(60)']
        process = real_popen(argv, **kwargs)
        processes.append(process)
        return process

    reader, writer = os.pipe()
    os.close(writer)
    with os.fdopen(reader, 'rb') as connection, patch('readiness_transfer.sys.stdin', connection), \
         patch('readiness_transfer.subprocess.Popen', side_effect=hanging_peer):
        with pytest.raises(RuntimeError, match='cancelled'):
            transfer(str(source), str(tmp_path), ['fake-ssh'])
    assert len(processes) == 2
    assert all(p.poll() is not None for p in processes)
