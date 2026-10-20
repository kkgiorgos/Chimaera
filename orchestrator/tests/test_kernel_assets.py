import io
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from agent import Agent
from common import atomic_json, digest
from kernel_assets import FILES, download, ensure, expose, smoke, verify


def pair(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (directory / name).write_text(name)
    atomic_json(directory / 'build.json', dict(kernel_release='5.15.180-chimaera',
        vermagic='5.15.180-chimaera SMP mod_unload modversions '))
    atomic_json(directory / 'kernel-assets.json', dict(version=1,
        sha256={name: digest(directory / name) for name in FILES}))
    return directory / 'kernel', directory / 'chimaera_dma.ko'


def test_export_and_atomic_import_verify_the_whole_pair(tmp_path):
    kernel, module = pair(tmp_path / 'producer/assets/kernel')
    producer = tmp_path / 'producer/experiment'
    producer.mkdir()
    expected = expose(producer, kernel, module)
    agent = Agent(tmp_path / 'consumer', tmp_path / 'consumer/data')
    incoming = Path(agent.asset_destination('experiment'))
    for source in (producer / 'export-assets').iterdir():
        shutil.copyfile(source, incoming / source.name)
    assert agent.commit_assets('experiment', expected)
    destination = agent.directory('experiment') / 'shared-assets'
    assert verify(destination, expected) == (destination / 'kernel', destination / 'chimaera_dma.ko')
    assert not incoming.exists()


@pytest.mark.parametrize('name', FILES)
def test_import_refuses_each_corrupt_file_and_preserves_existing_pair(tmp_path, name):
    agent = Agent(tmp_path, tmp_path / 'data')
    incoming = Path(agent.asset_destination('experiment'))
    pair(incoming)
    expected = digest(incoming / 'kernel-assets.json')
    destination = agent.directory('experiment') / 'shared-assets'
    pair(destination)
    (incoming / name).write_text('corruption')
    with pytest.raises(ValueError, match='asset differs'):
        agent.commit_assets('experiment', expected)
    verify(destination)
    assert incoming.exists()


def test_wrong_manifest_or_release_is_rejected(tmp_path):
    pair(tmp_path)
    with pytest.raises(ValueError, match='manifest differs'):
        verify(tmp_path, '0' * 64)
    atomic_json(tmp_path / 'build.json', dict(kernel_release='5.15.180', vermagic='another-release SMP'))
    record = json.loads((tmp_path / 'kernel-assets.json').read_text())
    record['sha256']['build.json'] = digest(tmp_path / 'build.json')
    atomic_json(tmp_path / 'kernel-assets.json', record)
    with pytest.raises(ValueError, match='release mismatch'):
        verify(tmp_path)


def test_download_verifies_content_and_never_publishes_partial_data(tmp_path):
    target = tmp_path / 'linux.tar.xz'
    expected = tmp_path / 'expected'
    expected.write_bytes(b'verified source')
    policy = dict(url='https://example.invalid/linux.tar.xz', sha256=digest(expected))
    target.write_bytes(b'old corrupt cache')
    with patch('kernel_assets.urllib.request.urlopen', return_value=io.BytesIO(b'bad source')):
        with pytest.raises(ValueError, match='checksum mismatch'):
            download(policy, target)
    assert target.read_bytes() == b'old corrupt cache'
    assert not list(tmp_path.glob('*.partial'))
    with patch('kernel_assets.urllib.request.urlopen', return_value=io.BytesIO(expected.read_bytes())):
        download(policy, target)
    with patch('kernel_assets.urllib.request.urlopen') as network:
        download(policy, target)
        network.assert_not_called()


def test_build_cache_invalidates_driver_and_checks_outputs(tmp_path):
    root = Path(__file__).resolve().parents[2]
    for directory in ('orchestrator/kernel', 'gem5-transport/kernel', 'gem5/include/gem5'):
        (tmp_path / directory).mkdir(parents=True)
    for name in ('Dockerfile', 'policy.json', 'config.fragment', 'build.py'):
        shutil.copyfile(root / 'orchestrator/kernel' / name, tmp_path / 'orchestrator/kernel' / name)
    driver = tmp_path / 'gem5-transport/kernel/chimaera_dma.c'
    driver.write_text('driver version one')
    output = tmp_path / 'validation'
    output.mkdir()
    builds = []
    mutate_sources = [False]

    def execute(command, **kwargs):
        if command[:2] == ['docker', 'run']:
            mount = next(c for c in command if c.endswith(',target=/output'))
            destination = Path(mount.split('source=', 1)[1].split(',target=')[0])
            pair(destination)
            builds.append(command)
            if mutate_sources[0]:
                driver.write_text('changed while compiling')
        return subprocess.CompletedProcess(command, 0, stdout='sha256:' + 'a' * 64)

    with patch('kernel_assets.run', return_value='sha256:' + 'a' * 64), \
         patch('kernel_assets.download'), patch('kernel_assets.subprocess.run', side_effect=execute):
        first = ensure(tmp_path, output, 2)
        assert ensure(tmp_path, output, 2) == first
        assert len(builds) == 1
        driver.write_text('driver version two')
        second = ensure(tmp_path, output, 4)
        assert second != first
        assert len(builds) == 2
        second[1].write_bytes(b'corrupt module')
        with pytest.raises(ValueError, match='asset differs'):
            ensure(tmp_path, output, 4)
        assert len(builds) == 2
        driver.write_text('driver version three')
        mutate_sources[0] = True
        with pytest.raises(ValueError, match='sources changed during compilation'):
            ensure(tmp_path, output, 4)
        assert len(list((tmp_path / '.chimaera-orchestrator/assets').glob('kernel-*/kernel-assets.json'))) == 2
    assert json.loads((tmp_path / '.chimaera-orchestrator/assets/current-kernel.json').read_text())['directory'] == str(second[0].parent)


@pytest.mark.parametrize('failure', [subprocess.CalledProcessError(1, ['docker']),
                                   subprocess.TimeoutExpired(['docker'], 1)])
def test_failed_boot_gate_cleans_up_container_and_retains_logs(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('CHIMAERA_KVM_GID', '109')
    with patch('kernel_assets.subprocess.run', side_effect=[failure, subprocess.CompletedProcess([], 0)]) as docker:
        with pytest.raises(RuntimeError, match='boot verification failed'):
            smoke(tmp_path, tmp_path, 'image', tmp_path / 'disk', tmp_path / 'kernel', tmp_path / 'driver')
    assert docker.call_args_list[-1].args[0][:3] == ['docker', 'rm', '-f']
    command = docker.call_args_list[0].args[0]
    assert command[command.index('--group-add') + 1] == '109'
    assert (tmp_path / 'kernel-smoke/launch.log').exists()
