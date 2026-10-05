import math
import xml.etree.ElementTree as ET
import numpy as np
import PyKDL as kdl
import pytest

from ball_catching_sim.scene import robot_urdf, generate, NEUTRAL
from ball_catching_robot.model import Arm, frame, vector


def test_fr3_tcp_and_inverse_kinematics():
    arm = Arm(robot_urdf())
    pose = arm.pose(NEUTRAL)
    np.testing.assert_allclose([pose.p[i] for i in range(3)], [.533165, 0, .730282], atol=1e-6)
    np.testing.assert_allclose([pose.M[i, 2] for i in range(3)], [0, 0, 1], atol=1e-10)
    target = [.58, .04, .73]
    solution = arm.inverse(target, pose.M, NEUTRAL)
    assert solution is not None
    result = arm.pose(solution)
    np.testing.assert_allclose([result.p[i] for i in range(3)], target, atol=.002)
    assert np.min(np.linalg.eigvalsh(arm.mass(solution))) > 0


def test_gravity_matches_potential_energy_including_fixed_branches():
    urdf = robot_urdf()
    arm = Arm(urdf)
    root = ET.fromstring(urdf)
    joints = {j.find('child').get('link'): j for j in root.findall('joint')}

    def potential(q):
        angles = dict(zip(arm.names, q))
        transforms = {'world': kdl.Frame.Identity()}

        def transform(name):
            if name in transforms:
                return transforms[name]
            joint = joints[name]
            result = transform(joint.find('parent').get('link')) * frame(joint.find('origin'))
            if joint.get('type') == 'revolute':
                result = result * kdl.Frame(kdl.Rotation.Rot(
                    kdl.Vector(*vector(joint.find('axis'), 'xyz', '0 0 1')), angles[joint.get('name')]))
            transforms[name] = result
            return result

        energy = 0.
        for link in root.findall('link'):
            inertial = link.find('inertial')
            if inertial is not None:
                cog = transform(link.get('name')) * frame(inertial.find('origin')).p
                energy += float(inertial.find('mass').get('value')) * 9.81 * cog.z()
        return energy

    q = np.array(NEUTRAL)
    gradient = []
    for i in range(7):
        delta = np.zeros(7)
        delta[i] = 1e-6
        gradient.append((potential(q + delta) - potential(q - delta)) / 2e-6)
    np.testing.assert_allclose(arm.forces(q, np.zeros(7)), gradient, atol=1e-7)


@pytest.mark.parametrize('options', [dict(launch_speed=0), dict(retention=-1),
                                    dict(launch_direction=[0, 0, 0]), dict(physics_step=math.nan),
                                    dict(initial_pose=[0]), dict(cup_radius=.02),
                                    dict(launch_position=[math.nan, 0, 1]), dict(camera_width=0),
                                    dict(initial_pose=[0, 0, 0, 0, 0, 0, 0]),
                                    dict(initial_pose=[0, -.785398, 0, -2.356194, .5, 1.570796, .785398])])
def test_invalid_scene_parameters_are_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        generate(tmp_path, **options)
