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


def bundled_config():
    return dict(repetitions=2, sweep=dict(sensor=[
        dict(lidar_hz=10, lidar_samples=360, gui=False),
        dict(lidar_hz=20, lidar_samples=720, gui=False)]))


@pytest.mark.parametrize('ids', [['unknown'], ['case_002_rep_01', 'case_001_rep_01'],
                                ['case_001_rep_01', 'case_001_rep_01']])
def test_runner_refuses_invalid_selection(tmp_path, ids):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(bundled_config()))
    result = subprocess.run([sys.executable, str(ROOT / 'ros/wall_follow_ws/scripts/run_experiments.py'),
        '--config', str(config), '--output', str(tmp_path / 'output'),
        '--dry-run', *[arg for value in ids for arg in ('--run-id', value)]], capture_output=True, text=True)
    assert result.returncode == 2
    assert not (tmp_path / 'output').exists()


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
        'ros2', 'launch', 'wall_follow_robot', 'robot.launch.py',
        'parameters_file:=/tmp/chimaera-controller.yaml', 'command_topic:=/robot/cmd_vel']


@pytest.mark.parametrize('layout', ['current', 'incomplete', 'corrupt'])
def test_guest_asset_runtime_validation(tmp_path, layout):
    from guest_assets import sha256, verify
    files = {name: {} for name in (
        'opt/chimaera/wall_follow/guest_start',
        'opt/chimaera/wall_follow/runtime/guest_bridge',
        'opt/chimaera/wall_follow/runtime/chimaera_ros/runner.py',
        'opt/chimaera/wall_follow/app/lib/wall_follow_robot/controller')}
    if layout == 'incomplete':
        files = {'usr/local/bin/chimaera_wall_follow_bridge': {}}
    (tmp_path / 'payload.json').write_text(json.dumps({'files': files}))
    for name in ('disk.img', 'kernel'):
        (tmp_path / name).write_bytes(b'test artifact')
    manifest = dict(schema_version=1,
                    layout=dict(root_partition=2, root_partuuid='4348494d-02'),
                    payload_sha256=sha256(tmp_path / 'payload.json'),
                    sha256={name: sha256(tmp_path / name) for name in ('disk.img', 'kernel')})
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    if layout == 'corrupt':
        (tmp_path / 'payload.json').write_text('{}')
    if layout == 'current':
        assert verify(tmp_path) == manifest
    else:
        with pytest.raises(ValueError, match='missing' if layout == 'incomplete' else 'checksum'):
            verify(tmp_path)
