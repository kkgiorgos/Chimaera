import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('launch_ros')
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument


@pytest.mark.parametrize('detailed,native_robot,clock', [
    (False, False, True), (True, False, False), (True, True, True)])
def test_only_native_launch_bridges_ros_clock(tmp_path, monkeypatch, detailed, native_robot, clock):
    path = Path(__file__).resolve().parents[1] / 'src/ball_catching_sim/launch/application.launch.py'
    spec = importlib.util.spec_from_file_location('ball_application_launch', path)
    application = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(application)
    monkeypatch.setattr(application, 'generate', lambda *args, **kwargs: (
        tmp_path / 'world.sdf', tmp_path / 'robot.urdf', '<robot name="test"/>', {}))
    monkeypatch.setattr(application, 'get_package_prefix', lambda package: str(tmp_path))
    monkeypatch.setattr(application, 'get_package_share_directory', lambda package: str(tmp_path))
    monkeypatch.setattr(application.tempfile, 'TemporaryDirectory', lambda **kwargs: SimpleNamespace(
        name=str(tmp_path), cleanup=lambda: None))
    nodes = []
    real_node = application.Node

    def node(**kwargs):
        nodes.append(kwargs)
        return real_node(**kwargs)

    monkeypatch.setattr(application, 'Node', node)
    context = LaunchContext()
    for action in application.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(robot=str(native_robot).lower(),
        detailed_timing_socket='/tmp/chimaera_time.sock' if detailed else '')
    application.start(context)
    bridge = next(node for node in nodes if node['package'] == 'ros_gz_bridge')
    assert sum(argument.startswith('/clock@') for argument in bridge['arguments']) == clock
    assert sum('image_raw@' in argument for argument in bridge['arguments']) == 2
    state = next(node for node in nodes if node['package'] == 'robot_state_publisher')
    assert state['parameters'][0]['use_sim_time'] is clock
