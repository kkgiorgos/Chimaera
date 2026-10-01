"""Exercise container integration with the repository's current runner and staging API."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
ROOT = DIRECTORY.parents[1]
sys.path.insert(0, str(DIRECTORY))
import worker


def bundled_config():
    return dict(repetitions=2, sweep=dict(sensor=[
        dict(lidar_hz=10, lidar_samples=360, gui=False),
        dict(lidar_hz=20, lidar_samples=720, gui=False)]))


@pytest.mark.parametrize('ids', [[], ['unknown'], ['case_002_rep_01', 'case_001_rep_01'],
                                ['case_001_rep_01', 'case_001_rep_01']])
def test_adapter_refuses_invalid_selection(tmp_path, ids):
    config, selection = tmp_path / 'config.json', tmp_path / 'ids.json'
    config.write_text(json.dumps(bundled_config()))
    selection.write_text(json.dumps(ids))
    result = subprocess.run([sys.executable, str(DIRECTORY / 'suite_adapter.py'),
        '--config', str(config), '--output', str(tmp_path / 'output'),
        '--run-ids-file', str(selection), '--dry-run'], capture_output=True, text=True)
    assert result.returncode == 2
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('local', [True, False])
def test_worker_command_is_accepted_by_current_runner(tmp_path, monkeypatch, local):
    config, selection = tmp_path / 'config.json', tmp_path / 'ids.json'
    config.write_text(json.dumps(bundled_config()))
    selection.write_text(json.dumps(['case_002_rep_02']))
    output = tmp_path / 'output'
    image_id = 'sha256:test'
    def docker(*arguments, **kwargs):
        if arguments[:2] == ('image', 'inspect'):
            return subprocess.CompletedProcess(arguments, 0, json.dumps([dict(
                Id=image_id, Architecture='amd64', Os='linux',
                Config=dict(Labels={'io.chimaera.run-selection': '2'}))]), '')
        return subprocess.CompletedProcess(arguments, 1, '', 'No such container')
    monkeypatch.setattr(worker, 'docker', docker)
    monkeypatch.setattr(worker.os, 'getuid', lambda: 1000)
    # Exercise validation, manifest recording and cleanup, stopping at Docker creation.
    with monkeypatch.context() as context:
        def unavailable(*args, **kwargs):
            raise OSError('test stops before Docker creation')
        context.setattr(worker.subprocess, 'Popen', unavailable)
        arguments = ['run', '--config', str(config), '--output', str(output),
                     '--architecture', 'cpu', '--run-ids-file', str(selection),
                     '--software-rendering']
        if local:
            arguments += ['--local']
        else:
            disk, kernel = tmp_path / 'disk.img', tmp_path / 'kernel'
            disk.touch(); kernel.touch()
            arguments += ['--guest-image', str(disk), '--kernel', str(kernel)]
            # Device access is independently checked by the real worker verifier.
            context.setattr(worker.stat, 'S_ISCHR', lambda _: True)
            original_stat = Path.stat
            context.setattr(worker.Path, 'stat', lambda p, **kw:
                           original_stat(kernel if str(p) == '/dev/kvm' else p, **kw))
            context.setattr(worker.os, 'access', lambda *_: True)
        with pytest.raises(OSError, match='test stops'):
            worker.run_worker(worker.parser().parse_args(arguments))
    recorded = json.loads((output / 'worker.json').read_text())['command']
    command = recorded[recorded.index(image_id) + 1:]
    replacements = {'/opt/chimaera/ws/scripts/suite_adapter.py': str(DIRECTORY / 'suite_adapter.py'),
                    '/assets/experiment.json': str(config), '/assets/run-ids.json': str(selection),
                    '/output/suite': str(tmp_path / 'dry-suite')}
    command = [replacements.get(value, value) for value in command] + ['--dry-run']
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan['selected_run_ids'] == ['case_002_rep_02']
    assert len(plan['runs']) == 1
    run = plan['runs'][0]
    assert run['id'] == 'case_002_rep_02' and run['repetition'] == 2
    assert run['parameters']['lidar_samples'] == 720
    assert run['sweep']['sensor']['lidar_hz'] == 20
    assert plan['deployment'] == ('local' if local else 'gem5')
    assert not (tmp_path / 'dry-suite').exists()


def test_guest_uses_current_session_staging(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge'))
    spec = importlib.util.spec_from_file_location('guest_session', DIRECTORY / 'scripts/guest_session.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sdk = tmp_path / 'sdk'
    shutil.copytree(ROOT / 'ros/wall_follow_ws/src/wall_follow_bridge/config',
                    sdk / 'ros/share/wall_follow_bridge/config')
    binaries = sdk / 'ros/lib/chimaera_ros_bridge'
    binaries.mkdir(parents=True)
    for name in ('guest_bridge', 'host_bridge', 'gazebo_host_bridge', 'validate_routes'):
        (binaries / name).write_text(name)
    root = module.stage_session(sdk, tmp_path / 'payload')
    assert (root / 'runtime/guest_bridge').read_text() == 'guest_bridge'
    assert (root / 'runtime/chimaera_ros/runner.py').is_file()
    assert (root / 'guest_start').stat().st_mode & 0o111
    assert not (root / 'app/lib/chimaera_ros_bridge/gazebo_host_bridge').exists()
    session = json.loads((root / 'session.json').read_text())
    assert session['deploy']['install_trees'] == []
    assert session['guest']['setup'][-1] == '/opt/chimaera/wall_follow/app/local_setup.bash'
    assert session['guest']['processes'][0]['command'] == [
        'ros2', 'launch', 'wall_follow_bridge', 'guest.launch.py']
