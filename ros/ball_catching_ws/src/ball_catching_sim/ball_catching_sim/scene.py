"""Generate a calibrated scene from the upstream FR3 URDF (no vendor edits)."""
from pathlib import Path
import math
import subprocess
import xml.etree.ElementTree as ET

import numpy as np
import xacro
from ament_index_python.packages import get_package_share_directory

NEUTRAL = [0.0, -math.pi / 4, 0.0, -3 * math.pi / 4, 0.0, math.pi / 2, math.pi / 4]


def child(parent, tag, text=None, **attributes):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attributes.items()})
    if text is not None:
        e.text = str(text)
    return e


def numbers(values):
    return ' '.join(str(float(x)) for x in values)


def robot_urdf(radius=0.12, depth=0.14):
    description = Path(get_package_share_directory('franka_description'))
    document = xacro.process_file(str(description / 'robots/fr3/fr3.urdf.xacro'),
                                  mappings={'hand': 'false', 'gazebo': 'true',
                                            'ros2_control': 'false'})
    robot = ET.fromstring(document.toxml())
    # Absolute paths work both in the renderer and the standalone robot process.
    for mesh in robot.findall('.//mesh'):
        mesh.set('filename', mesh.get('filename').replace('package://franka_description/',
                                                        str(description) + '/'))
    cup = child(robot, 'link', name='catch_cup')
    inertial = child(cup, 'inertial')
    child(inertial, 'origin', xyz=f'0 0 {depth / 2}', rpy='0 0 0')
    child(inertial, 'mass', value='0.25')
    child(inertial, 'inertia', ixx='0.002', iyy='0.002', izz='0.003',
          ixy='0', ixz='0', iyz='0')
    parts = [('bottom', [0, 0, 0], [2 * radius, 2 * radius, 0.008], 0)]
    # A hollow polygon made of boxes: convex mesh collision would fill the cup.
    for i in range(20):
        angle = i * 2 * math.pi / 20
        parts.append((f'wall_{i}', [radius * math.cos(angle), radius * math.sin(angle), depth / 2],
                      [0.008, 2 * radius * math.tan(math.pi / 20) + 0.004, depth], angle))
    for name, xyz, size, angle in parts:
        for kind in ['collision', 'visual']:
            part = child(cup, kind, name=name + '_' + kind)
            child(part, 'origin', xyz=numbers(xyz), rpy=f'0 0 {angle}')
            child(child(part, 'geometry'), 'box', size=numbers(size))
            if kind == 'visual':
                child(child(part, 'material', name='cup_blue'), 'color', rgba='0.08 0.3 0.8 1')
    joint = child(robot, 'joint', name='cup_mount', type='fixed')
    child(joint, 'parent', link='fr3_link8')
    child(joint, 'child', link='catch_cup')
    # Offset the opening beyond the wrist: flipping a cup directly over the
    # downward flange would put the robot wrist in the ball's entry path.
    child(joint, 'origin', xyz='0.16 -0.16 0', rpy=f'{math.pi} 0 0')
    bracket = child(robot, 'link', name='cup_bracket')
    inertial = child(bracket, 'inertial')
    child(inertial, 'origin', xyz='0.08 -0.08 0', rpy='0 0 0')
    child(inertial, 'mass', value='0.08')
    child(inertial, 'inertia', ixx='0.0004', iyy='0.0004', izz='0.0008', ixy='0', ixz='0', iyz='0')
    for kind in ['visual', 'collision']:
        part = child(bracket, kind, name='bracket')
        child(part, 'origin', xyz='0.08 -0.08 0', rpy=f'0 0 {-math.pi / 4}')
        child(child(part, 'geometry'), 'box', size='0.23 0.018 0.012')
        if kind == 'visual':
            child(child(part, 'material', name='bracket_grey'), 'color', rgba='0.3 0.3 0.3 1')
    joint = child(robot, 'joint', name='bracket_mount', type='fixed')
    child(joint, 'parent', link='fr3_link8')
    child(joint, 'child', link='cup_bracket')
    child(joint, 'origin', xyz='0 0 0', rpy='0 0 0')
    child(robot, 'gazebo', reference='cup_mount')
    # Keep the named cup link available to the independent ground-truth scorer.
    robot.find("gazebo[@reference='cup_mount']").append(ET.fromstring(
        '<preserveFixedJoint>true</preserveFixedJoint>'))
    child(robot, 'link', name='cup_rim')
    joint = child(robot, 'joint', name='cup_rim_joint', type='fixed')
    child(joint, 'parent', link='catch_cup')
    child(joint, 'child', link='cup_rim')
    child(joint, 'origin', xyz=f'0 0 {depth}', rpy='0 0 0')
    return ET.tostring(robot, encoding='unicode')


def generate(directory, *, initial_pose=NEUTRAL, launch_position=(1.8, 0, 1.6),
             launch_direction=(-1, 0, 0), launch_speed=3.0, retention=1.0,
             camera_hz=90.0, camera_width=640, camera_height=480,
             physics_step=0.001, cup_radius=0.12, cup_depth=0.14,
             auto_throw=True, trial_timeout=4.0, output=''):
    if len(initial_pose) != 7 or not all(math.isfinite(x) for x in initial_pose):
        raise ValueError('initial_pose must contain seven finite joint angles')
    if len(launch_position) != 3 or len(launch_direction) != 3:
        raise ValueError('launch position and direction must have three components')
    direction = np.asarray(launch_direction, dtype=float)
    if not np.isfinite(direction).all() or np.linalg.norm(direction) < 1e-9:
        raise ValueError('launch_direction must be a finite nonzero vector')
    for name, value in dict(launch_speed=launch_speed, retention=retention,
                            camera_hz=camera_hz, physics_step=physics_step,
                            cup_radius=cup_radius, cup_depth=cup_depth,
                            trial_timeout=trial_timeout).items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} must be finite and positive')
    if cup_radius <= 0.0335 or cup_depth <= 0.067:
        raise ValueError('cup must accommodate the tennis ball')
    velocity = direction / np.linalg.norm(direction) * launch_speed
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    urdf = robot_urdf(cup_radius, cup_depth)
    robot_path = directory / 'robot.urdf'
    robot_path.write_text(urdf)
    model_xml = subprocess.run(['ign', 'sdf', '-p', str(robot_path)],
                               check=True, capture_output=True, text=True).stdout
    sdf = ET.Element('sdf', version='1.8')
    world = child(sdf, 'world', name='ball_catching')
    child(world, 'gravity', '0 0 -9.81')
    physics = child(world, 'physics', name='physics', type='ignored')
    child(physics, 'max_step_size', physics_step)
    child(physics, 'real_time_factor', '1')
    for filename, name in [('physics', 'Physics'), ('user-commands', 'UserCommands'),
                           ('scene-broadcaster', 'SceneBroadcaster'), ('sensors', 'Sensors')]:
        plugin = child(world, 'plugin', filename=f'ignition-gazebo-{filename}-system',
                       name=f'ignition::gazebo::systems::{name}')
        if name == 'Sensors':
            child(plugin, 'render_engine', 'ogre2')
    scene = child(world, 'scene')
    child(scene, 'ambient', '0.7 0.7 0.7 1')
    child(scene, 'background', '0.15 0.15 0.18 1')
    light = child(world, 'light', name='sun', type='directional')
    child(light, 'pose', '0 0 5 0 0 0')
    child(light, 'diffuse', '0.8 0.8 0.8 1')
    child(light, 'direction', '-0.3 0.1 -1')
    floor = child(world, 'model', name='floor')
    child(floor, 'static', 'true')
    link = child(floor, 'link', name='floor')
    for kind in ['collision', 'visual']:
        shape = child(link, kind, name='floor')
        plane = child(child(shape, 'geometry'), 'plane')
        child(plane, 'normal', '0 0 1')
        child(plane, 'size', '10 10')
        if kind == 'visual':
            child(child(shape, 'material'), 'diffuse', '0.25 0.25 0.25 1')
    arm = ET.fromstring(model_xml).find('model')
    arm.set('name', 'fr3')
    world.append(arm)
    ball = child(world, 'model', name='tennis_ball')
    # Park outside the camera field until the host launches it.
    child(ball, 'pose', '-5 0 0.04 0 0 0')
    link = child(ball, 'link', name='ball')
    inertial = child(link, 'inertial')
    child(inertial, 'mass', '0.057')
    inertia = child(inertial, 'inertia')
    moment = 2 / 3 * 0.057 * 0.0335 ** 2
    for tag in ['ixx', 'iyy', 'izz']:
        child(inertia, tag, moment)
    for kind in ['collision', 'visual']:
        part = child(link, kind, name='ball')
        child(child(child(part, 'geometry'), 'sphere'), 'radius', '0.0335')
        if kind == 'visual':
            material = child(part, 'material')
            child(material, 'diffuse', '0.75 1 0.01 1')
            child(material, 'ambient', '0.75 1 0.01 1')
        else:
            surface = child(part, 'surface')
            bounce = child(surface, 'bounce')
            child(bounce, 'restitution_coefficient', '0.35')
            child(bounce, 'threshold', '0.05')
            friction = child(child(surface, 'friction'), 'ode')
            child(friction, 'mu', '0.6')
            child(friction, 'mu2', '0.6')
    # Parallel cameras looking along world +x. Optical axes: right=-y, down=-z.
    camera_x, camera_z, baseline, hfov = -0.65, 1.0, 0.30, 1.35
    for side, y in [('left', baseline / 2), ('right', -baseline / 2)]:
        camera = child(world, 'model', name=side + '_camera')
        child(camera, 'static', 'true')
        child(camera, 'pose', f'{camera_x} {y} {camera_z} 0 0 0')
        sensor = child(child(camera, 'link', name='camera'), 'sensor', name=side, type='camera')
        child(sensor, 'always_on', 'true')
        child(sensor, 'update_rate', camera_hz)
        child(sensor, 'topic', f'/stereo/{side}/image_raw')
        spec = child(sensor, 'camera')
        child(spec, 'horizontal_fov', hfov)
        image = child(spec, 'image')
        child(image, 'width', camera_width)
        child(image, 'height', camera_height)
        child(image, 'format', 'R8G8B8')
        clip = child(spec, 'clip')
        child(clip, 'near', '0.05')
        child(clip, 'far', '8')
    plugin = child(world, 'plugin', filename='libball_catching_host.so',
                   name='ball_catching::Host')
    for name, value in dict(initial_pose=numbers(initial_pose), launch_position=numbers(launch_position),
                            launch_velocity=numbers(velocity), retention=retention,
                            cup_radius=cup_radius, cup_depth=cup_depth,
                            auto_throw=str(auto_throw).lower(), trial_timeout=trial_timeout,
                            output=output).items():
        child(plugin, name, value)
    path = directory / 'world.sdf'
    path.write_text(ET.tostring(sdf, encoding='unicode'))
    focal = camera_width / (2 * math.tan(hfov / 2))
    calibration = dict(focal=focal, cx=camera_width / 2, cy=camera_height / 2,
                       baseline=baseline, camera_origin=[camera_x, baseline / 2, camera_z])
    return path, robot_path, urdf, calibration
