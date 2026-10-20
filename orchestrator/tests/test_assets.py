import json
from unittest.mock import patch

import pytest

from agent import Agent
from assets import clean_unused
from common import atomic_json


def test_kernel_cache_preserves_current_and_saved_pairs(tmp_path):
    directories = []
    for char in 'abc':
        directory = tmp_path / 'assets' / ('kernel-' + char * 64)
        directory.mkdir(parents=True)
        (directory / 'kernel').write_bytes(b'kernel')
        (directory / 'chimaera_dma.ko').write_bytes(b'module')
        directories.append(directory)
    atomic_json(tmp_path / 'assets/current-kernel.json', dict(directory=str(directories[0])))
    atomic_json(tmp_path / 'experiments/e/inputs/runner.json', dict(
        mounts=[dict(source=str(directories[1] / 'kernel'))]))
    with patch('assets.run', side_effect=['', '']):
        result = clean_unused(tmp_path)
    assert result['kernels'] == 1
    assert directories[0].exists() and directories[1].exists()
    assert not directories[2].exists()


def test_cleanup_preserves_saved_experiments_containers_and_configured_disks(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    disks = {}
    for name in ('saved', 'container', 'configured', 'unused'):
        disks[name] = assets / ('ball-catching-' + name + '.img')
        disks[name].write_bytes(b'disk')
        atomic_json(assets / ('disk-provenance-' + name + '.json'), {})
    saved, container, unused, imported = ['sha256:' + c * 64 for c in 'abcd']
    atomic_json(tmp_path / 'experiments/e/inputs/runner.json', dict(image=saved,
        mounts=[dict(source=str(disks['saved']))]))
    atomic_json(assets / ('image-' + 'd' * 64 + '.json'), dict(image_id=imported))
    listing = '\n'.join(json.dumps(dict(Repository='chimaera', Tag='ball-' + str(i), ID=image))
                        for i, image in enumerate((saved, container, unused)))
    listing += '\n' + json.dumps(dict(Repository='<none>', Tag='<none>', ID=imported))
    def docker(argv, **kwargs):
        if argv[:3] == ['docker', 'ps', '-aq']:
            return 'container'
        if argv[:2] == ['docker', 'inspect']:
            return json.dumps([dict(Image=container, Mounts=[dict(Source=str(disks['container']))])])
        if argv[:3] == ['docker', 'image', 'ls']:
            return listing
        return ''
    with patch('assets.run', side_effect=docker) as commands:
        result = clean_unused(tmp_path, [str(disks['configured'])])
    assert result['images'] == 2
    assert result['disks'] == 1
    assert all(disks[name].exists() for name in ('saved', 'container', 'configured'))
    assert not disks['unused'].exists()
    assert not (assets / 'disk-provenance-unused.json').exists()
    removed = [c.args[0] for c in commands.call_args_list if c.args[0][:3] == ['docker', 'image', 'rm']]
    assert removed == [['docker', 'image', 'rm', 'chimaera:ball-2'], ['docker', 'image', 'rm', imported]]
    assert not any('--force' in c for c in removed)


def test_cleanup_fails_closed_on_malformed_saved_manifest(tmp_path):
    manifest = tmp_path / 'experiments/e/ready.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{broken')
    with patch('assets.run') as docker:
        with pytest.raises(ValueError):
            clean_unused(tmp_path)
        docker.assert_not_called()


@pytest.mark.parametrize('name', ['ready.json', 'request.json'])
def test_cleanup_retains_calibration_images_for_resume(tmp_path, name):
    sdk, app = ['sha256:' + c * 64 for c in 'ab']
    record = dict(provenance=dict(sdk_image_id=sdk, application_image_id=app))
    if name == 'request.json':
        record = dict(calibration=record)
    atomic_json(tmp_path / 'experiments/e' / name, record)
    for image in (sdk, app):
        atomic_json(tmp_path / 'assets' / ('image-' + image[7:] + '.json'), dict(image_id=image))
    listing = '\n'.join(json.dumps(dict(Repository='<none>', Tag='<none>', ID=image))
                        for image in (sdk, app))
    with patch('assets.run', side_effect=['', listing]) as docker:
        assert clean_unused(tmp_path)['images'] == 0
    assert not any(c.args[0][:3] == ['docker', 'image', 'rm'] for c in docker.call_args_list)


def test_agent_refuses_cleanup_during_preparation(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    agent.store.put('tasks', 'prep', dict(id='prep', kind='prepare', status='running'))
    with patch('assets.clean_unused') as clean:
        with pytest.raises(ValueError, match='tasks to stop'):
            agent.rpc('clean_assets')
        clean.assert_not_called()


def test_import_registry_tracks_images_and_rejects_mutable_references(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    image = 'sha256:' + 'a' * 64
    assert agent.rpc('remember_image', image=image)
    markers = list((agent.data / 'assets').glob('image-*.json'))
    assert len(markers) == 1
    assert json.loads(markers[0].read_text()) == dict(image_id=image)
    with pytest.raises(ValueError, match='image ID'):
        agent.rpc('remember_image', image='mutable:tag')


def test_cleanup_removes_transfer_tag_with_other_tags_for_unused_image(tmp_path):
    image = 'sha256:' + 'a' * 64
    tags = ['ball-unused', 'transfer-' + 'a' * 64]
    listing = '\n'.join(json.dumps(dict(Repository='chimaera', Tag=tag, ID=image)) for tag in tags)
    with patch('assets.run', side_effect=['', listing, '']) as docker:
        assert clean_unused(tmp_path)['images'] == 1
    assert docker.call_args_list[-1].args[0] == ['docker', 'image', 'rm',
        *sorted('chimaera:' + tag for tag in tags)]


def test_inventory_is_read_only_and_reports_allocated_disk_and_image_sizes(tmp_path):
    from assets import asset_inventory
    disk = tmp_path / 'assets/ball-catching-unused.img'
    disk.parent.mkdir()
    with disk.open('wb') as stream:
        stream.truncate(1024 ** 3)
    image = 'sha256:' + 'a' * 64
    listing = json.dumps(dict(Repository='chimaera', Tag='ball-unused', ID=image, Size='1.5GB'))
    stale = disk.parent / ('image-' + 'b' * 64 + '.json')
    atomic_json(stale, dict(image_id='sha256:' + 'b' * 64))
    with patch('assets.run', side_effect=['', listing]) as docker:
        items = asset_inventory(tmp_path)
    assert items[0]['bytes'] == 1500000000
    assert items[1]['bytes'] == disk.stat().st_blocks * 512
    assert items[1]['bytes'] < disk.stat().st_size
    assert not any(item['protected'] for item in items)
    assert stale.exists()
    assert docker.call_count == 2


def test_selected_cleanup_removes_only_requested_disk(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    for name in ('chosen', 'retained'):
        (assets / f'ball-catching-{name}.img').write_bytes(b'guest')
    with patch('assets.run', side_effect=['', '']):
        result = clean_unused(tmp_path, asset_ids=['ball-catching-chosen.img'])
    assert result['disks'] == 1
    assert not (assets / 'ball-catching-chosen.img').exists()
    assert (assets / 'ball-catching-retained.img').exists()


@pytest.mark.parametrize('selection', [[], ['../../outside'], ['ball-catching-protected.img'],
                                       ['ball-catching-unused.img', 'missing']])
def test_selected_cleanup_revalidates_all_assets_before_deleting(tmp_path, selection):
    assets = tmp_path / 'assets'
    assets.mkdir()
    protected = assets / 'ball-catching-protected.img'
    protected.write_bytes(b'guest')
    unused = assets / 'ball-catching-unused.img'
    unused.write_bytes(b'guest')
    with patch('assets.run', side_effect=['', '']):
        with pytest.raises(ValueError):
            clean_unused(tmp_path, keep_paths=[protected], asset_ids=selection)
    assert protected.exists() and unused.exists()


def test_allocated_bytes_ignores_symlink_targets_and_counts_hardlinks_once(tmp_path):
    import os
    from assets import allocated_bytes
    directory = tmp_path / 'experiment'
    directory.mkdir()
    file = directory / 'data'
    file.write_bytes(b'content')
    os.link(file, directory / 'hardlink')
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'large').write_bytes(b'x' * 16384)
    (directory / 'link').symlink_to(external, target_is_directory=True)
    assert allocated_bytes(directory) == file.stat().st_blocks * 512


def test_agent_storage_and_selected_cleanup_use_rpc_contract(tmp_path):
    agent = Agent(tmp_path, tmp_path / 'data')
    disk = agent.data / 'assets/ball-catching-unused.img'
    disk.parent.mkdir()
    disk.write_bytes(b'guest')
    with patch('assets.run', side_effect=['', '', '', '']):
        result = agent.rpc('storage')
        assert result['filesystem']['free'] > 0
        assert result['assets'][0]['id'] == disk.name
        assert agent.rpc('clean_assets', asset_ids=[disk.name])['disks'] == 1


def test_selected_image_cleanup_keeps_other_images(tmp_path):
    images = ['sha256:' + c * 64 for c in 'ab']
    listing = '\n'.join(json.dumps(dict(Repository='chimaera', Tag='ball-' + str(i), ID=image))
                        for i, image in enumerate(images))
    with patch('assets.run', side_effect=['', listing, '']) as docker:
        result = clean_unused(tmp_path, asset_ids=[images[1]])
    assert result['images'] == 1
    assert docker.call_args_list[-1].args[0] == ['docker', 'image', 'rm', 'chimaera:ball-1']


def test_allocated_bytes_does_not_follow_root_symlink(tmp_path):
    from assets import allocated_bytes
    target = tmp_path / 'target'
    target.mkdir()
    (target / 'data').write_bytes(b'large' * 4096)
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    assert allocated_bytes(link) == 0
