import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from common import atomic_json, build_identities, digest
from pipeline import prepare

SDK_ID = 'sha256:' + 'a' * 64
APP_ID = 'sha256:' + 'b' * 64
APP_KEY = hashlib.sha256(('app-key' + SDK_ID).encode()).hexdigest()[:16]


def test_sdk_cache_depends_on_build_content_not_repo_commit_or_dashboard(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    files = {'gem5/src/cpu.cc': 'cpu', 'containers/Dockerfile': 'sdk',
             'containers/ball-catching/Dockerfile': 'app',
             'ros/ball_catching_ws/src/robot.cc': 'robot',
             'orchestrator/backend/pipeline.py': 'orchestration', 'README.md': 'docs'}
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    initial = build_identities(tmp_path)
    (tmp_path / 'README.md').write_text('new docs')
    (tmp_path / 'orchestrator/backend/pipeline.py').write_text('new orchestration')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'fixture'], check=True)
    assert build_identities(tmp_path) == initial
    (tmp_path / 'ros/ball_catching_ws/src/robot.cc').write_text('updated robot')
    application = build_identities(tmp_path)
    assert application['sdk'] == initial['sdk']
    assert application['application'] != initial['application']
    (tmp_path / 'gem5/src/cpu.cc').write_text('updated simulator')
    assert build_identities(tmp_path)['sdk'] != initial['sdk']


def pipeline_fixture(tmp_path):
    directory = tmp_path / '.chimaera-orchestrator/experiments/experiment'
    identity = dict(revision='revision', fingerprint='sources')
    kernel = tmp_path / 'kernel'
    kernel.write_bytes(b'pinned kernel')
    disk = tmp_path / 'disk.img'
    disk.write_bytes(b'local disk differs from reference filesystem bytes')
    records = dict(stream=dict(sustainable_gbps=40))
    provenance = dict(sdk_image_id=SDK_ID, application_image_id=APP_ID, source_identity=identity, revision='revision', disk_sha256='other-host-disk',
                      kernel_sha256=digest(kernel), input_hashes={'experiment.json': 'portable'})
    request = dict(config={'ramulator_config': 'memory.json'}, source_identity=identity, cpus=1,
                   memory='2g', build_jobs=4, calibration=dict(records=records, provenance=provenance),
                   disk=str(disk), kernel=str(kernel))
    atomic_json(directory / 'request.json', request)

    def execute(argv, **kwargs):
        if '--skip-build' in argv:
            atomic_json(directory / 'inputs/inputs.json', dict(sha256={
                'source-experiment.json': 'local-path', 'experiment.json': 'portable'}))
            atomic_json(directory / 'inputs/plan.json', dict(points=[]))
            atomic_json(directory / 'inputs/prepared/calibration/stream/calibration.json', records['stream'])
        image_id = APP_ID if argv[-1] == APP_ID or str(argv[-1]).startswith('chimaera:ball-') else SDK_ID
        return subprocess.CompletedProcess(argv, 0, stdout=image_id if '--format' in argv else '')

    return directory, identity, disk, kernel, execute


def test_preparation_reuses_images_and_builds_guest_locally_with_shared_calibration(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=execute) as commands, \
         patch('guest.ensure_assets', return_value=(disk, kernel)) as assets:
        prepare(directory)
    assets.assert_called_once_with(tmp_path, directory, SDK_ID, str(disk), str(kernel))
    assert not any('buildx' in call.args[0] for call in commands.call_args_list)
    assert not any(any(str(arg).endswith('/containers/build.py') for arg in call.args[0]) for call in commands.call_args_list)
    ready = json.loads((directory / 'ready.json').read_text())
    assert ready['provenance']['disk_sha256'] == digest(disk)
    assert not (directory / 'export-assets').exists()


def test_checkpoint_consumer_imports_producer_disk_and_never_recreates_readiness(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['readiness_checkpoint'] = True
    request['calibration']['provenance'].update(
        disk_sha256=digest(disk), checkpoint_index_sha256='shared-checkpoints', readiness_checkpoint=True)
    atomic_json(directory / 'request.json', request)
    imported = directory / 'imported'
    imported.mkdir()
    (imported / 'guest.img').write_bytes(disk.read_bytes())
    (imported / 'kernel').write_bytes(kernel.read_bytes())
    def checkpoint_execute(argv, **kwargs):
        result = execute(argv, **kwargs)
        if '--skip-build' in argv:
            atomic_json(directory / 'inputs/runner.json', dict(image=APP_ID, devices=['/dev/kvm'],
                env={'CHIMAERA_CHECKPOINT_ROOT': '/assets/readiness/checkpoints'},
                mounts=[dict(source='assets', target='/assets/readiness')]))
        return result
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=checkpoint_execute) as commands, \
         patch('guest.ensure_assets') as assets, \
         patch('checkpoints.unpack', return_value=imported) as unpack, \
         patch('checkpoints.package') as package:
        prepare(directory)
    assets.assert_not_called()
    package.assert_not_called()
    unpack.assert_called_once_with(directory, APP_ID, 'shared-checkpoints')
    setup = next(call.args[0] for call in commands.call_args_list if '--skip-build' in call.args[0])
    assert '--readiness-from' in setup and '--checkpoint-create' not in setup
    runner = json.loads((directory / 'inputs/runner.json').read_text())
    assert runner['devices'] == []
    assert runner['env']['CHIMAERA_CHECKPOINT_ROOT'] == '/assets/readiness/checkpoints'
    assert any(mount['target'] == '/assets/readiness' for mount in runner['mounts'])
    ready = json.loads((directory / 'ready.json').read_text())
    assert ready['provenance']['readiness_checkpoint'] is True


def test_checkpoint_consumer_rejects_preparation_without_a_shared_checkpoint(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['readiness_checkpoint'] = True
    atomic_json(directory / 'request.json', request)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=execute), \
         patch('guest.ensure_assets') as assets:
        with pytest.raises(ValueError, match='lacks its readiness checkpoint'):
            prepare(directory)
    assets.assert_not_called()


def test_checkpoint_producer_freezes_the_prepared_image_and_removes_case_kvm(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request.update(readiness_checkpoint=True, calibration=None)
    atomic_json(directory / 'request.json', request)
    final_image = APP_ID
    def checkpoint_execute(argv, **kwargs):
        result = execute(argv, **kwargs)
        if '--skip-build' in argv:
            atomic_json(directory / 'inputs/runner.json', dict(image=APP_ID, devices=['/dev/kvm'],
                env={}, mounts=[]))
        return result
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=checkpoint_execute) as commands, \
         patch('guest.ensure_assets', return_value=(disk, kernel)), \
         patch('checkpoints.package', return_value=(final_image, 'frozen-index')) as package:
        prepare(directory)
    setup = next(call.args[0] for call in commands.call_args_list if '--skip-build' in call.args[0])
    assert '--checkpoint-create' in setup and '--readiness-from' not in setup
    package.assert_called_once_with(directory, APP_ID, disk, kernel)
    runner = json.loads((directory / 'inputs/runner.json').read_text())
    ready = json.loads((directory / 'ready.json').read_text())
    assert runner['devices'] == []
    assert runner['image'] == ready['image'] == final_image
    assert ready['provenance']['application_image_id'] == final_image
    assert ready['provenance']['checkpoint_index_sha256'] == 'frozen-index'


@pytest.mark.parametrize('field', ['revision', 'fingerprint'])
def test_source_mismatch_reports_queued_and_local_values_before_using_images(tmp_path, monkeypatch, field):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    local = dict(identity, **{field: 'different-local-value'})
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=local), \
         patch('pipeline.build_identities') as builds, \
         patch('pipeline.subprocess.run') as commands, \
         patch('guest.ensure_assets') as assets:
        with pytest.raises(ValueError) as error:
            prepare(directory)
        message = str(error.value)
        assert f'{field}: queued={identity[field]}, local=different-local-value' in message
        assert 'worker checkout, not the transferred Docker images' in message
        assert 'queue a new experiment' in message
        builds.assert_not_called()
        commands.assert_not_called()
        assets.assert_not_called()
    assert not (directory / 'ready.json').exists()


def test_preparation_still_rejects_different_shared_kernel(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['calibration']['provenance']['kernel_sha256'] = 'incompatible kernel'
    atomic_json(directory / 'request.json', request)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk', application='app')), \
         patch('pipeline.subprocess.run', side_effect=execute), \
         patch('guest.ensure_assets', return_value=(disk, kernel)):
        with pytest.raises(ValueError, match='kernel differ'):
            prepare(directory)


def test_ignored_external_robot_assets_are_part_of_application_cache(tmp_path):
    root = tmp_path / 'ros/franka_ws/src/franka_description'
    root.mkdir(parents=True)
    mesh = root / 'robot.dae'
    mesh.write_text('original mesh')
    initial = build_identities(tmp_path)
    mesh.write_text('different mesh')
    assert build_identities(tmp_path)['application'] != initial['application']
    # Compiled output and Python caches must not cause gem5 recompilation.
    for relative in ('gem5/build/X86/gem5.opt', 'gem5/__pycache__/module.pyc', 'gem5/resources/guest.img'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'generated output')
    assert build_identities(tmp_path)['sdk'] == initial['sdk']


def test_preparation_rejects_different_build_sources_before_docker_or_assets(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['calibration']['provenance']['build_inputs'] = dict(sdk='reference-sdk', application='reference-app')
    atomic_json(directory / 'request.json', request)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='local-sdk', application='local-app')), \
         patch('pipeline.subprocess.run') as commands, \
         patch('guest.ensure_assets') as assets:
        with pytest.raises(ValueError, match='sdk: calibration=reference-sdk, local=local-sdk'):
            prepare(directory)
        commands.assert_not_called()
        assets.assert_not_called()
    assert not (directory / 'shared-calibration').exists()


def test_host_editor_caches_and_generated_parser_tables_do_not_change_build_keys(tmp_path):
    source = tmp_path / 'gem5/src/cpu.cc'
    source.parent.mkdir(parents=True)
    source.write_text('simulator source')
    initial = build_identities(tmp_path)
    for name in ('gem5/.cache/clangd/index/cpu.idx', 'gem5/.vscode/settings.json',
                 'gem5/m5_outputs/test/stats.txt', 'gem5/m5out/config.json',
                 'gem5/src/arch/parser.out', 'gem5/src/mem/slicc/parsetab.py',
                 'gem5-transport/.cache/clangd/index/transport.idx',
                 'ros/ball_catching_ws/src/.cache/clangd/index/robot.idx'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('host-local generated data')
    assert build_identities(tmp_path) == initial
    source.write_text('changed simulator source')
    assert build_identities(tmp_path)['sdk'] != initial['sdk']


def test_consumer_never_builds_sdk_when_transfer_is_missing(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk', application='app')), \
         patch('pipeline.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as commands, \
         patch('guest.ensure_assets') as assets:
        with pytest.raises(ValueError, match='complete image transfer'):
            prepare(directory)
        assert len(commands.call_args_list) == 1
        assets.assert_not_called()


@pytest.mark.parametrize('present', [True, False])
def test_consumer_uses_shared_application_without_building(tmp_path, monkeypatch, present):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['calibration']['provenance']['application_image_id'] = APP_ID
    atomic_json(directory / 'request.json', request)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    def inspect(argv, **kwargs):
        if not present and argv == ['docker', 'image', 'inspect', APP_ID]:
            return subprocess.CompletedProcess(argv, 1)
        return execute(argv, **kwargs)
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=inspect) as commands, \
         patch('guest.ensure_assets', return_value=(disk, kernel)) as assets:
        if present:
            prepare(directory)
            assert assets.call_args.args[2] == SDK_ID
            ready = json.loads((directory / 'ready.json').read_text())
            assert ready['image'] == APP_ID
            assert ready['provenance']['application_image_id'] == APP_ID
        else:
            with pytest.raises(ValueError, match='complete image transfer'):
                prepare(directory)
            assets.assert_not_called()
        assert not any('buildx' in c.args[0] for c in commands.call_args_list)


def test_producer_records_both_images_for_distribution(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['calibration'] = None
    atomic_json(directory / 'request.json', request)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('images.build', return_value=(SDK_ID, SDK_ID, APP_ID)), \
         patch('assets.clean_unused'), \
         patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=execute), \
         patch('guest.ensure_assets', return_value=(disk, kernel)):
        prepare(directory)
    ready = json.loads((directory / 'ready.json').read_text())
    assert ready['provenance']['sdk_image_id'] == SDK_ID
    assert ready['provenance']['application_image_id'] == APP_ID
    assert len(list((tmp_path / '.chimaera-orchestrator/assets').glob('image-*.json'))) == 2
