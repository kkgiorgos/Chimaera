import math
import pytest
from ball_catching_sim.scene import generate, home_pose, robot_urdf


@pytest.mark.parametrize('options', [dict(launch_speed=0), dict(duration=0), dict(duration=-1), dict(duration=math.nan),
                                    dict(duration=math.inf),
                                    dict(launch_direction=[0, 0, 0]), dict(physics_step=math.nan),
                                    dict(initial_pose=[0]), dict(cup_radius=.02),
                                    dict(launch_position=[math.nan, 0, 1]), dict(camera_width=0),
                                    dict(initial_pose=[0, 0, 0, 0, 0, 0, 0]),
                                    dict(initial_pose=[0, -.785398, 0, -2.356194, .5, 1.570796, .785398])])
def test_invalid_scene_parameters_are_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        generate(tmp_path, **options)


def test_prepared_catching_pose_is_rejected(tmp_path):
    prepared = [-1.37723, -1.10247, .784482, -2.10488, 2.74689, 2.49007, 2.19599]
    with pytest.raises(ValueError, match='fixed upright home'):
        generate(tmp_path, initial_pose=prepared)
    assert not (tmp_path/'robot.urdf').exists()


def test_cup_cannot_start_from_the_previous_bent_home(tmp_path):
    with pytest.raises(ValueError, match='fixed upright home'):
        generate(tmp_path, initial_pose=[0., -math.pi/4, 0., -3*math.pi/4, 0., math.pi/2, math.pi/4])
    assert not (tmp_path/'robot.urdf').exists()


def test_generated_robot_has_a_hollow_cup_and_seven_actuated_joints():
    import xml.etree.ElementTree as ET
    robot = ET.fromstring(robot_urdf())
    assert robot.find("link[@name='catch_cup']") is not None
    assert robot.find("link[@name='cup_rim']") is not None
    assert len(robot.findall("link[@name='catch_cup']/collision")) == 21
    assert len(robot.findall("joint[@type='revolute']")) == 7
    assert robot.findall("joint[@type='prismatic']") == []
    assert robot.find("link[@name='fr3_hand']") is None


def test_scene_matches_the_cup_model_and_calibration(tmp_path):
    import xml.etree.ElementTree as ET
    world_path, robot_path, urdf, calibration = generate(tmp_path)
    world = ET.parse(world_path).getroot().find('world')
    arm = world.find("model[@name='fr3']")
    assert arm.find("link[@name='catch_cup']") is not None
    assert arm.findall("joint[@type='revolute']") and len(arm.findall("joint[@type='revolute']")) == 7
    plugin = world.find("plugin[@name='ball_catching::Host']")
    assert [float(q) for q in plugin.findtext('initial_pose').split()] == home_pose()
    assert float(plugin.findtext('duration')) == 1.5
    assert robot_path.read_text() == urdf
    assert calibration['baseline'] == .3
    assert calibration['camera_origin'] == [-.65, .15, 1.]
