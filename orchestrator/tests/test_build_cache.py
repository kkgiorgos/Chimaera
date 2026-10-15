import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from common import atomic_json, build_identities, digest
from pipeline import prepare


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
    directory = tmp_path / 'experiment'
    identity = dict(revision='revision', fingerprint='sources')
    kernel = tmp_path / 'kernel'
    kernel.write_bytes(b'pinned kernel')
    disk = tmp_path / 'disk.img'
    disk.write_bytes(b'local disk differs from reference filesystem bytes')
    records = dict(stream=dict(sustainable_gbps=40))
    provenance = dict(source_identity=identity, revision='revision', disk_sha256='other-host-disk',
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
        return subprocess.CompletedProcess(argv, 0)

    return directory, identity, disk, kernel, execute


def test_preparation_reuses_images_and_builds_guest_locally_with_shared_calibration(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('pipeline.source_identity', return_value=identity), \
         patch('pipeline.build_identities', return_value=dict(sdk='sdk-key', application='app-key')), \
         patch('pipeline.subprocess.run', side_effect=execute) as commands, \
         patch('guest.ensure_assets', return_value=(disk, kernel)) as assets:
        prepare(directory)
    assets.assert_called_once_with(tmp_path, directory, 'chimaera:ball-app-key', str(disk), str(kernel))
    assert not any('buildx' in call.args[0] for call in commands.call_args_list)
    assert not any(any(str(arg).endswith('/containers/build.py') for arg in call.args[0]) for call in commands.call_args_list)
    ready = json.loads((directory / 'ready.json').read_text())
    assert ready['provenance']['disk_sha256'] == digest(disk)
    assert not (directory / 'export-assets').exists()


def test_preparation_still_rejects_different_shared_kernel(tmp_path, monkeypatch):
    directory, identity, disk, kernel, execute = pipeline_fixture(tmp_path)
    request = json.loads((directory / 'request.json').read_text())
    request['calibration']['provenance']['kernel_sha256'] = 'incompatible kernel'
    atomic_json(directory / 'request.json', request)
    monkeypatch.setenv('CHIMAERA_REPO', str(tmp_path))
    with patch('pipeline.source_identity', return_value=identity), \
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
