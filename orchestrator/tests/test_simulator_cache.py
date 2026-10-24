"""Simulator checkpoints survive BuildKit GC and independent SDK revisions."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from common import atomic_json, build_identities
from images import SDK, build, save_simulator, simulator_artifacts


def fixture_sources(root):
    files = {
        'containers/Dockerfile': 'FROM ubuntu AS dependencies\nFROM dependencies AS gem5-build\n'
            'RUN compile\n# Optional reuse of already built\nFROM dependencies AS gem5-prebuilt\n'
            'FROM gem5-build AS bridge-build\nRUN bridge\nFROM dependencies AS image\n',
        'containers/Dockerfile.dockerignore': '**\n',
        'containers/ball-catching/Dockerfile': 'ARG SDK_IMAGE=x\nARG APPLICATION_BASE=dependencies\n'
            'FROM ${SDK_IMAGE} AS dependencies\nRUN deps\nFROM ${APPLICATION_BASE} AS image\n',
        'gem5/src/cpu/kvm/base.cc': 'simulator',
        'ros/chimaera_ros_ws/src/bridge.cpp': 'bridge',
        'ros/ball_catching_ws/src/robot.cpp': 'robot',
        'containers/scripts/compile-gem5.sh': 'compile',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    atomic_json(root / 'containers/profiles/jammy-humble-fortress.json',
                dict(platform='linux/amd64', base_image='ubuntu', ros_distro='humble'))


def fake_docker(commands, tags):
    serial = 0
    def execute(argv, **kwargs):
        nonlocal serial
        serial += 1
        commands.append(argv)
        stdout = ''
        if argv[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in tags else 1,
                                               stdout=tags.get(argv[-1], ''))
        if '--tag' in argv or '-t' in argv:
            flag = '--tag' if '--tag' in argv else '-t'
            tags[argv[argv.index(flag) + 1]] = 'sha256:' + str(serial).zfill(64)
        if argv[:2] == ['docker', 'commit']:
            tags[argv[-1]] = 'sha256:' + str(serial).zfill(64)
        if argv[:2] == ['docker', 'create']:
            stdout = 'export-container'
        if argv[:2] == ['docker', 'cp']:
            destination = Path(argv[-1])
            names = ('build/X86/gem5.opt', 'util/m5/build/x86/out/m5',
                     'util/m5/build/x86/out/libm5.a') if destination.name == 'gem5' else (
                         'libramulator.so', 'DDR4.json', 'LICENSE')
            for name in names:
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('installed artifact ' + name)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)
    return execute


def test_bridge_changes_reuse_verified_simulator_even_after_builder_prune(tmp_path):
    fixture_sources(tmp_path)
    commands, tags = [], {}
    with patch('images.subprocess.run', side_effect=fake_docker(commands, tags)):
        builds = build_identities(tmp_path)
        build(tmp_path, builds, 2)
        assert any(c[:3] == ['docker', 'buildx', 'prune'] for c in commands)
        cache = tmp_path / '.chimaera-orchestrator/assets'
        artifacts = simulator_artifacts(cache, builds['simulator'])
        assert artifacts
        assert not (artifacts / 'gem5/src').exists()
        commands.clear()
        (tmp_path / 'ros/chimaera_ros_ws/src/bridge.cpp').write_text('new bridge')
        changed = build_identities(tmp_path)
        assert changed['simulator'] == builds['simulator']
        assert changed['sdk'] != builds['sdk']
        build(tmp_path, changed, 4)
        sdk_build = next(c for c in commands if '--tag' in c and SDK in c)
        assert sdk_build[-2:] == ['--gem5-artifacts', str(artifacts)]
        base_build = next(c for c in commands if '--load' in c)
        assert 'GEM5_STAGE=gem5-prebuilt' in base_build
        assert 'gem5-artifacts=' + str(artifacts) in base_build
        assert not any(c[:2] == ['docker', 'cp'] for c in commands)
        # Losing the Docker slot also uses the checkpoint instead of compiling.
        del tags[SDK]
        commands.clear()
        build(tmp_path, changed, 4)
        assert '--gem5-artifacts' in next(c for c in commands if '--tag' in c and SDK in c)
        # A corrupt checkpoint is recovered from an unchanged matching SDK.
        (artifacts / 'ramulator2/libramulator.so').write_text('corrupt')
        commands.clear()
        build(tmp_path, changed, 4)
        assert not any('--tag' in c for c in commands)
        assert any(c[:2] == ['docker', 'cp'] for c in commands)
        commands.clear()
        (tmp_path / 'gem5/src/cpu/kvm/base.cc').write_text('fixed simulator')
        changed = build_identities(tmp_path)
        assert changed['simulator'] != builds['simulator']
        build(tmp_path, changed, 2)
        sdk_build = next(c for c in commands if '--tag' in c and SDK in c)
        assert '--gem5-artifacts' not in sdk_build
        assert simulator_artifacts(cache, changed['simulator'])


@pytest.mark.parametrize('source', [
    'gem5/src/cpu/kvm/base.cc', 'containers/scripts/compile-gem5.sh',
    'containers/scripts/install-build-env.py', 'containers/profiles/jammy-humble-fortress.json',
    'containers/Dockerfile.dockerignore',
])
def test_simulator_input_changes_invalidate_checkpoint(tmp_path, source):
    fixture_sources(tmp_path)
    initial = build_identities(tmp_path)
    path = tmp_path / source
    path.write_text('new input')
    assert build_identities(tmp_path)['simulator'] != initial['simulator']


def test_bridge_recipe_changes_do_not_invalidate_simulator(tmp_path):
    fixture_sources(tmp_path)
    path = tmp_path / 'containers/Dockerfile'
    initial = build_identities(tmp_path)
    path.write_text(path.read_text().replace('RUN bridge', 'RUN new-bridge'))
    assert build_identities(tmp_path)['simulator'] == initial['simulator']
    path.write_text(path.read_text().replace('RUN compile', 'RUN new-compiler'))
    assert build_identities(tmp_path)['simulator'] != initial['simulator']


def test_corrupt_checkpoint_is_rejected_and_failed_export_keeps_previous(tmp_path):
    commands = []
    with patch('images.subprocess.run', side_effect=fake_docker(commands, {})):
        directory = save_simulator(tmp_path, 'old', 'sdk')
    assert simulator_artifacts(tmp_path, 'old') == directory
    with patch('images.run', return_value='container'), \
         patch('images.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'cp')):
        with pytest.raises(subprocess.CalledProcessError):
            save_simulator(tmp_path, 'new', 'sdk')
    assert simulator_artifacts(tmp_path, 'old') == directory
    (directory / 'gem5/build/X86/gem5.opt').write_text('corrupt')
    assert simulator_artifacts(tmp_path, 'old') is None
    assert json.loads((directory.parent / 'manifest.json').read_text())['key'] == 'old'
