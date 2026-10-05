import math
import pytest
from ball_catching_sim.scene import generate


@pytest.mark.parametrize('options', [dict(launch_speed=0), dict(retention=-1),
                                    dict(launch_direction=[0, 0, 0]), dict(physics_step=math.nan),
                                    dict(initial_pose=[0]), dict(cup_radius=.02),
                                    dict(launch_position=[math.nan, 0, 1]), dict(camera_width=0),
                                    dict(initial_pose=[0, 0, 0, 0, 0, 0, 0]),
                                    dict(initial_pose=[0, -.785398, 0, -2.356194, .5, 1.570796, .785398])])
def test_invalid_scene_parameters_are_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        generate(tmp_path, **options)
