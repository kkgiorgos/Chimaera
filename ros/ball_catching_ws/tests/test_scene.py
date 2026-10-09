import math
import pytest
from ball_catching_sim.scene import generate, NEUTRAL


@pytest.mark.parametrize('options', [dict(launch_speed=0), dict(retention=-1),
                                    dict(launch_direction=[0, 0, 0]), dict(physics_step=math.nan),
                                    dict(initial_pose=[0]), dict(cup_radius=.02),
                                    dict(launch_position=[math.nan, 0, 1]), dict(camera_width=0),
                                    dict(initial_pose=[0, 0, 0, 0, 0, 0, 0]),
                                    dict(initial_pose=[0, -.785398, 0, -2.356194, .5, 1.570796, .785398])])
def test_invalid_scene_parameters_are_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        generate(tmp_path, **options)


@pytest.mark.parametrize('mode', ['cup', 'gripper'])
def test_prepared_catching_pose_is_rejected_for_every_mode(tmp_path, mode):
    prepared = [-1.37723, -1.10247, .784482, -2.10488, 2.74689, 2.49007, 2.19599]
    with pytest.raises(ValueError, match='fixed upright home'):
        generate(tmp_path, mode=mode, initial_pose=prepared)
    assert not (tmp_path/'robot.urdf').exists()


def test_cup_cannot_start_from_the_previous_bent_home(tmp_path):
    with pytest.raises(ValueError, match='fixed upright home'):
        generate(tmp_path, mode='cup', initial_pose=NEUTRAL)
    assert not (tmp_path/'robot.urdf').exists()
