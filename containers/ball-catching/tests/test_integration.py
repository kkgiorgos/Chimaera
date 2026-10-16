"""Exercise portable exports and isolated worker/report handoff without booting gem5."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
ROOT = DIRECTORY.parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


configure = module('ball_configure', DIRECTORY / 'configure.py')
worker = module('ball_worker', DIRECTORY / 'worker.py')
compare = module('ball_compare', DIRECTORY / 'compare.py')
runner = module('generic_runner_for_ball', DIRECTORY.parent / 'run.py')


def test_export_freezes_the_exact_ten_case_matrix_and_portable_mounts(tmp_path):
    disk, kernel = tmp_path / 'disk.img', tmp_path / 'kernel'
    disk.write_bytes(b'guest')
    kernel.write_bytes(b'kernel')
    inputs = tmp_path / 'inputs'
    config = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    configure.configure(config, inputs, 'sha256:fixed-image', disk, kernel)
    exported = runner.load(inputs / 'runner.json')
    assert len(exported['jobs']) == 10
    assert exported['image'] == 'sha256:fixed-image'
    assert exported['devices'] == ['/dev/kvm']
    assert all(Path(m['source']).exists() for m in exported['mounts'])
    assert json.loads((inputs / 'experiment.json').read_text())['ramulator_config'] == '/assets/inputs/memory.json'
    record = json.loads((inputs / 'inputs.json').read_text())
    for path, expected in record['sha256'].items():
        assert worker.digest(inputs / path) == expected
    points = json.loads((inputs / 'plan.json').read_text())['points']
    assert [p['pattern'] for p in points] == ['none'] + ['stream']*3 + ['stride']*3 + ['random']*3
    fake_device = tmp_path / 'kvm'
    fake_device.touch()
    exported['devices'] = [str(fake_device)]
    commands = [runner.command(exported, job, tmp_path / job['name'], 'sha256:fixed-image', job['name'])
                for job in exported['jobs'][:2]]
    for command in commands:
        assert command[command.index('--network')+1] == 'none'
        assert command[command.index('--ipc')+1] == 'private'
        assert '--read-only' in command
        assert 'readonly' in ' '.join(command)
    assert commands[0][-1] == 'case-000' and commands[1][-1] == 'case-001'
    assert commands[0] != commands[1]
    assert not (inputs / 'prepared').exists()


def inputs_record(tmp_path):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'inputs.json').write_text(json.dumps(dict(sha256={}, source_revision='snapshot')))
    return inputs


def test_preparation_only_calibrates_memory_and_packages_guest(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    (inputs / 'experiment.json').write_text(json.dumps(dict(patterns=['stream', 'random'])))
    commands = []
    monkeypatch.setattr(worker.subprocess, 'run', lambda command, **kw: commands.append(command))
    worker.prepare(inputs, tmp_path / 'prepared')
    assert '--calibrate-only' in commands[0]
    assert 'check_aggressor_rates.py' in commands[1][1]
    assert '--prepare-only' in commands[2] and '--resume' in commands[2]
    assert all('--source-revision' in c for c in (commands[0], commands[2]))
    assert all('--case' not in c for c in commands)


def test_producer_prepares_readiness_once_after_packaging(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    (inputs / 'experiment.json').write_text(json.dumps(dict(patterns=['stream'])))
    commands = []
    monkeypatch.setattr(worker.subprocess, 'run', lambda command, **kw: commands.append(command))
    worker.prepare(inputs, tmp_path / 'prepared', checkpoint_create=True)
    assert '--checkpoint-create' in commands[-1]
    assert '--resume' in commands[-1]
    assert sum('--checkpoint-create' in command for command in commands) == 1
    assert all('--case' not in command for command in commands)


def test_missing_required_checkpoint_never_falls_back_to_kvm(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    monkeypatch.setenv('CHIMAERA_CHECKPOINT_ROOT', str(tmp_path / 'missing'))
    with pytest.raises(ValueError, match='Required readiness checkpoints are missing'):
        worker.run_case(inputs, tmp_path / 'out', 'case-000')
    assert not (tmp_path / 'out').exists()


def test_worker_reuses_bundle_and_shared_calibration_then_resumes_one_case(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    prepared = inputs / 'prepared'
    prepared.mkdir()
    (prepared / 'calibration').mkdir()
    (prepared / 'guest.tar.gz').write_bytes(b'guest bundle')
    (prepared / 'routes.json').write_text('{}')
    (prepared / 'experiment_manifest.json').write_text(json.dumps(dict(points=[dict(id='case-002')])) )
    output = tmp_path / 'output'
    calls = []
    monkeypatch.setattr(worker.subprocess, 'call', lambda command: calls.append(command) or 0)
    assert worker.run_case(inputs, output, 'case-002') == 0
    assert (output / 'guest.tar.gz').read_bytes() == b'guest bundle'
    assert (output / 'calibration').is_symlink()
    assert calls[0][-3:] == ['--resume', '--case', 'case-002']
    assert worker.run_case(inputs, output, 'case-002') == 0
    assert len(calls) == 2
    with pytest.raises(ValueError, match='Unknown case'):
        worker.run_case(inputs, tmp_path / 'invalid', 'case-999')
    assert not (tmp_path / 'invalid').exists()


def test_changed_frozen_inputs_fail_before_execution(tmp_path):
    inputs = inputs_record(tmp_path)
    (inputs / 'experiment.json').write_text('{}')
    (inputs / 'inputs.json').write_text(json.dumps(dict(sha256={'experiment.json': 'wrong'}, source_revision='snapshot')))
    with pytest.raises(ValueError, match='Frozen input changed'):
        worker.common(inputs, tmp_path / 'out')


def test_aggregate_keeps_baseline_completed_and_failed_cases(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    points = [dict(id=f'case-{i:03d}', status='pending', trials=[]) for i in range(3)]
    (inputs / 'plan.json').write_text(json.dumps(dict(points=points)))
    output = tmp_path / 'results'
    for i, status in enumerate(('complete', 'infrastructure_failure')):
        suite = output / 'jobs' / f'case-{i:03d}' / 'suite'
        suite.mkdir(parents=True)
        trials = ['case-000-attempt-001'] if i == 0 else []
        (suite / 'experiment_manifest.json').write_text(json.dumps(dict(points=[dict(
            id=f'case-{i:03d}', status=status, trials=trials, reason='saved failure' if i else '')])))
    captured = []
    monkeypatch.setattr(compare, 'create_dashboard', lambda *args: captured.append(args))
    compare.collect(inputs, output)
    directories, destination, reported = captured[0]
    assert len(directories) == 1 and directories[0].name == 'case-000-attempt-001'
    assert [p['status'] for p in reported] == ['complete', 'infrastructure_failure', 'pending']
    assert reported[1]['reason'] == 'saved failure'
    assert destination == output / 'comparison/dashboard.html'


@pytest.mark.parametrize('cpus,memory', [(0, '12g'), (float('nan'), '12g'), (4, 'none')])
def test_bad_resource_limits_fail_before_writing_inputs(tmp_path, cpus, memory):
    with pytest.raises(ValueError, match='resource limits'):
        configure.configure(Path('unused'), tmp_path / 'inputs', 'image', Path('disk'), Path('kernel'), cpus, memory)
    assert not (tmp_path / 'inputs').exists()


def test_prebuilt_sdk_export_hashes_required_artifacts(tmp_path):
    build = module('generic_build_for_ball', DIRECTORY.parent / 'build.py')
    root = tmp_path / 'gem5'
    for name in ('build/X86/gem5.opt', 'util/m5/build/x86/out/m5', 'util/m5/build/x86/out/libm5.a',
                 'ext/ramulator2/ramulator2/libramulator.so', 'src/mem/ramulator2/LICENSE',
                 'ext/ramulator2/configs/memory.json'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    output = tmp_path / 'export'
    output.mkdir()
    build.stage_gem5(root, output)
    files = json.loads((output / 'prebuilt-gem5.json').read_text())['files']
    assert len(files) == 6
    for path, digest in files.items():
        assert build._digest(output / path) == digest
