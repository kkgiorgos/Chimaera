"""The default workflow prepares images and selects a reusable immutable guest."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
import guest_assets

spec = importlib.util.spec_from_file_location('container_build', DIRECTORY / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.mark.parametrize('failure', [None, 'worker', 'alias'])
def test_default_build_prepares_everything_and_stops_on_failure(monkeypatch, failure):
    monkeypatch.setattr(sys, 'argv', ['build.py', '--jobs', '2', '--no-cache'])
    monkeypatch.setattr(build, 'build_input_digest', lambda *_: 'test-inputs')
    monkeypatch.setattr(build.subprocess, 'check_output', lambda args, **kw:
                        'revision\n' if args[1] == 'rev-parse' else '')
    phases = []

    def run(command, **kwargs):
        if command[:2] == ['docker', 'tag']:
            phase = 'alias'
        elif command[0] == 'docker':
            phase = command[command.index('--target') + 1]
            assert '--no-cache' in command
            assert '--provenance=false' in command
            assert 'BUILD_JOBS=2' in command
        else:
            assert command[1] == str(DIRECTORY / 'prepare-guest.py')
            phase = 'guest'
        phases.append(phase)
        if phase == failure:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build.subprocess, 'run', run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            build.main()
        assert phases == (['worker'] if failure == 'worker' else ['worker', 'alias'])
    else:
        build.main()
        assert phases == ['worker', 'alias', 'guest']


def test_selection_preserves_previous_guest_and_resolves_new_default(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_assets, '__file__', str(tmp_path / 'guest_assets.py'))
    assets = tmp_path / 'guest-assets'
    old, new = assets / 'old', assets / 'new'
    old.mkdir(parents=True)
    new.mkdir()
    (old / 'disk.img').write_bytes(b'original guest')
    guest_assets.select('test-profile', old, 'old-image')
    assert guest_assets.selected('test-profile') == old
    guest_assets.select('test-profile', new, 'new-image')
    assert guest_assets.selected('test-profile', 'new-image') == new
    with pytest.raises(ValueError, match='different image'):
        guest_assets.selected('test-profile', 'old-image')
    assert (old / 'disk.img').read_bytes() == b'original guest'
    assert not list(assets.glob('*.tmp'))


def test_missing_or_invalid_selection_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_assets, '__file__', str(tmp_path / 'guest_assets.py'))
    with pytest.raises(ValueError, match='build.py first'):
        guest_assets.selected('test-profile')
    pointer = guest_assets.selection_file('test-profile')
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({'directory': '../outside'}))
    with pytest.raises(ValueError, match='Invalid guest selection'):
        guest_assets.selected('test-profile')


def test_default_demo_preview_needs_no_setup_and_writes_nothing(tmp_path):
    output = tmp_path / 'demo'
    result = subprocess.run([sys.executable, str(DIRECTORY / 'run-suite.py'),
                             '--output', str(output), '--dry-run'],
                            capture_output=True, text=True, check=True)
    preview = json.loads(result.stdout)
    assert preview['plan']['architecture'] == 'baseline'
    assert preview['jobs'] and len(preview['jobs']) == len(preview['plan']['runs'])
    assert preview['requested_workers'] == 1
    assert not output.exists()
