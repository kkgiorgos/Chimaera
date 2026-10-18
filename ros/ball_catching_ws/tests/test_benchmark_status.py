from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark_status
import run_memory_suite as runner


@pytest.mark.parametrize('endpoint', [0, 1, 2])
def test_occupied_transport_paths_are_preserved(tmp_path, monkeypatch, endpoint):
    paths = tuple(tmp_path / name for name in ('g2h', 'h2g', 'time'))
    monkeypatch.setattr(benchmark_status, 'LOCAL_TRANSPORT_PATHS', paths)
    benchmark_status.check_local_transport()
    paths[endpoint].write_bytes(b'occupied')
    with pytest.raises(RuntimeError, match='Local Chimaera socket paths already exist'):
        benchmark_status.check_local_transport()
    assert paths[endpoint].read_bytes() == b'occupied'


def test_dangling_symlink_is_an_occupied_transport_path(tmp_path, monkeypatch):
    path = tmp_path / 'g2h'
    path.symlink_to(tmp_path / 'missing')
    monkeypatch.setattr(benchmark_status, 'LOCAL_TRANSPORT_PATHS', (path,))
    with pytest.raises(RuntimeError, match=str(path)):
        benchmark_status.check_local_transport()
    assert path.is_symlink()


def test_preflight_stops_before_output_or_simulation(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'g2h'
    path.write_bytes(b'occupied')
    monkeypatch.setattr(benchmark_status, 'LOCAL_TRANSPORT_PATHS', (path,))
    output = tmp_path / 'suite'
    monkeypatch.setattr(runner, 'calibrate', lambda *args: pytest.fail('Do not calibrate with occupied sockets'))
    monkeypatch.setattr(runner, 'prepare_bundle', lambda *args: pytest.fail('Do not stage a blocked guest'))
    monkeypatch.setattr(sys, 'argv', ['run_memory_suite.py', '--output', str(output), '--case', 'case-003'])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert 'Local Chimaera socket paths already exist' in capsys.readouterr().err
    assert not output.exists()
    assert path.read_bytes() == b'occupied'


def test_launch_validation_distinguishes_failed_and_incomplete_checks(tmp_path):
    path = tmp_path / 'launch_validation.json'
    assert benchmark_status.launch_failure(path) is None
    path.write_text('{"valid":')
    assert benchmark_status.launch_failure(path) is None
    path.write_text('{"valid":true,"reason":"ballistic_launch_verified"}')
    assert benchmark_status.launch_failure(path) is None
    path.write_text('{"valid":false,"reason":"launch_pose_or_velocity_reset"}')
    assert benchmark_status.launch_failure(path) == 'Invalid ball launch: launch_pose_or_velocity_reset'


def test_simulator_assertion_is_reported_before_the_cascading_bridge_disconnect(tmp_path):
    log = tmp_path / 'launch.log'
    failure = ("[gem5.opt-5] gem5.opt: src/cpu/simple/timing.cc:832: "
        "void gem5::TimingSimpleCPU::completeIfetch(gem5::PacketPtr): "
        "Assertion `_status == IcacheWaitResponse' failed.")
    log.write_text(failure + '\n[gazebo_host_bridge-4] Host bridge: gem5 timing: '
        'timing server disconnected before replying\n')
    assert benchmark_status.host_failure(log) == failure


def test_checkpoint_bridge_failure_stops_a_host_that_has_not_exited(tmp_path, monkeypatch):
    import types
    scene = types.ModuleType('ball_catching_sim.scene')
    scene.generate = lambda *args, **kwargs: (None, tmp_path / 'robot.urdf', None, {})
    monkeypatch.setitem(sys.modules, 'ball_catching_sim', types.ModuleType('ball_catching_sim'))
    monkeypatch.setitem(sys.modules, 'ball_catching_sim.scene', scene)
    directory = tmp_path / 'checkpoint'
    stopped = []

    class RunningHost:
        def __init__(self, *args, **kwargs):
            (directory / 'launch.log').write_text(
                '[gazebo_host_bridge-4] Host bridge: gem5 timing: socket timeout; simulator state is unknown\n')

        def poll(self):
            return None

    fixed = dict(duration=1, warmup=0, physics_step=.0005, joint_hz=1000,
        camera_position=[-2, 0, 2], camera_width=480, camera_height=360,
        cup_radius=.12, cup_depth=.14, wall_timeout_s=43200)
    memory = tmp_path / 'memory.json'
    memory.write_text('{}')
    data = dict(fixed=fixed, hardware={}, aggressor={}, ramulator_config=str(memory))
    monkeypatch.setattr(runner, 'session', lambda *args: {'bridge': {}})
    monkeypatch.setattr(runner.subprocess, 'Popen', RunningHost)
    monkeypatch.setattr(runner, 'stop', lambda process: stopped.append(process))
    args = SimpleNamespace(image='guest.img', kernel='kernel', gem5_root='gem5')
    point = dict(pattern='none', pressure=0, rates={'camera_hz': 30}, throw={})
    with pytest.raises(RuntimeError, match='socket timeout'):
        runner.gem5_trial(directory, tmp_path, data, point, args, {}, tmp_path / 'guest.tar.gz',
                          checkpoint_prepare=directory)
    assert len(stopped) == 1
    assert not (directory / 'checkpoint.json').exists()
