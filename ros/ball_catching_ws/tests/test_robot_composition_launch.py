import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('launch_ros')
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument


@pytest.mark.parametrize('composed', [False, True])
def test_execution_layout_preserves_robot_parameters(composed, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'src/ball_catching_robot/launch/robot.launch.py'
    spec = importlib.util.spec_from_file_location('robot_composition_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(composed=str(composed).lower(),
        robot_file='/tmp/robot.urdf', gem5_clock_library='/tmp/clock.so',
        gem5_time_origin_file='/tmp/origin', control_hz='250', camera_hz='30')
    created = []
    original = module.Node
    def node(**kwargs):
        result = original(**kwargs)
        created.append((result, kwargs))
        return result
    monkeypatch.setattr(module, 'Node', node)
    actions = module.start(context)
    selected = [kwargs for action, kwargs in created if action in actions]
    assert [item['executable'] for item in selected] == (
        ['robot_composed'] if composed else ['perception', 'intercept', 'effort_control'])
    control = selected[0 if composed else 2]['parameters'][0]
    assert control['control_hz'] == 250.
    assert control['robot_file'] == '/tmp/robot.urdf'
    assert control['gem5_clock_library'] == '/tmp/clock.so'
    assert control['gem5_time_origin_file'] == '/tmp/origin'
    assert selected[0]['parameters'][0]['camera_hz'] == 30.
