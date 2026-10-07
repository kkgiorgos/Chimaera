"""Generate a calibrated scene from the upstream FR3 URDF (no vendor edits)."""
from pathlib import Path
import math
import subprocess
import xml.etree.ElementTree as ET

import numpy as np
import xacro
from ament_index_python.packages import get_package_share_directory, get_package_prefix
from ball_catching_sim.flight import BALL_RADIUS, COURT_LENGTH, COURT_WIDTH, NET_HEIGHT, drag_factor

NEUTRAL = [0.0, -math.pi / 4, 0.0, -3 * math.pi / 4, 0.0, math.pi / 2, math.pi / 4]


def child(parent, tag, text=None, **attributes):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attributes.items()})
    if text is not None:
        e.text = str(text)
    return e


def numbers(values):
    return ' '.join(str(float(x)) for x in values)


def robot_urdf(radius=0.12, depth=0.14, mode='cup'):
    if mode not in ('cup', 'gripper'):
        raise ValueError('mode must be cup or gripper')
    description = Path(get_package_share_directory('franka_description'))
    document = xacro.process_file(str(description / 'robots/fr3/fr3.urdf.xacro'),
                                  mappings={'hand': str(mode == 'gripper').lower(), 'gazebo': 'true',
                                            'ros2_control': 'false'})
    robot = ET.fromstring(document.toxml())
    # Absolute paths work both in the renderer and the standalone robot process.
    for mesh in robot.findall('.//mesh'):
        mesh.set('filename', mesh.get('filename').replace('package://franka_description/',
                                                        str(description) + '/'))
    if mode == 'gripper':
        for name in ('fr3_finger_joint1', 'fr3_finger_joint2'):
            finger = robot.find(f"joint[@name='{name}']")
            # Drive each physical finger explicitly. URDF mimic does not supply
            # a motor coupling in Fortress's effort-controlled physics.
            mimic = finger.find('mimic')
            if mimic is not None:
                finger.remove(mimic)
            finger.find('limit').set('velocity', '0.05')
            finger.find('limit').set('effort', '35')
        child(robot, 'link', name='grasp_center')
        joint = child(robot, 'joint', name='grasp_center_joint', type='fixed')
        child(joint, 'parent', link='fr3_hand')
        child(joint, 'child', link='grasp_center')
        child(joint, 'origin', xyz='0 0 0.10365', rpy='0 0 0')
        child(child(robot, 'gazebo', reference='fr3_hand_joint'), 'preserveFixedJoint', 'true')
        return ET.tostring(robot, encoding='unicode')
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
             auto_throw=True, trial_timeout=4.0, output='', mode='cup', court=False,
             drag_coefficient=0., air_density=1.225, grip_friction=1.0, present=False, allowed_bounces=0,
             court_restitution=.745, court_tangent_ratio=.6, camera_position=(-.65, 0., 1.)):
    if len(initial_pose) != 7 or not all(math.isfinite(x) for x in initial_pose):
        raise ValueError('initial_pose must contain seven finite joint angles')
    if any(abs(q-home) > 1e-8 for q, home in zip(initial_pose, NEUTRAL)):
        raise ValueError('Every trial must start from the fixed upright home pose')
    if len(launch_position) != 3 or len(launch_direction) != 3:
        raise ValueError('launch position and direction must have three components')
    if not all(math.isfinite(x) for x in launch_position):
        raise ValueError('launch_position must contain finite values')
    if camera_width < 64 or camera_height < 64:
        raise ValueError('camera dimensions must be at least 64 pixels')
    if len(camera_position) != 3 or not all(math.isfinite(x) for x in camera_position):
        raise ValueError('camera_position needs three finite coordinates')
    direction = np.asarray(launch_direction, dtype=float)
    if not np.isfinite(direction).all() or np.linalg.norm(direction) < 1e-9:
        raise ValueError('launch_direction must be a finite nonzero vector')
    for name, value in dict(launch_speed=launch_speed, retention=retention,
                            camera_hz=camera_hz, physics_step=physics_step,
                            cup_radius=cup_radius, cup_depth=cup_depth,
                            trial_timeout=trial_timeout).items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} must be finite and positive')
    if not math.isfinite(grip_friction) or grip_friction <= 0 or grip_friction > 2:
        raise ValueError('grip friction must be finite in (0, 2]')
    if allowed_bounces not in range(5) or (allowed_bounces and (not court or mode != 'gripper')):
        raise ValueError('zero to four bounces require a gripper court scene')
    if not all(math.isfinite(x) and 0 < x <= 1 for x in (court_restitution, court_tangent_ratio)):
        raise ValueError('court impact coefficients must be in (0, 1]')
    drag = drag_factor(drag_coefficient, air_density)
    if cup_radius <= 0.0335 or cup_depth <= 0.067:
        raise ValueError('cup must accommodate the tennis ball')
    velocity = direction / np.linalg.norm(direction) * launch_speed
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    urdf = robot_urdf(cup_radius, cup_depth, mode)
    robot_path = directory / 'robot.urdf'
    robot_path.write_text(urdf)
    validator = Path(get_package_prefix('ball_catching_robot')) / 'lib/ball_catching_robot/validate_model'
    validation = subprocess.run([str(validator), str(robot_path), *map(str, initial_pose)],
                                capture_output=True, text=True)
    if validation.returncode:
        raise ValueError(validation.stderr.strip())
    model_xml = subprocess.run(['ign', 'sdf', '-p', str(robot_path)],
                               check=True, capture_output=True, text=True).stdout
    sdf = ET.Element('sdf', version='1.8')
    world = child(sdf, 'world', name='ball_catching')
    child(world, 'gravity', '0 0 -9.81')
    physics = child(world, 'physics', name='physics', type='ignored')
    child(physics, 'max_step_size', physics_step)
    child(physics, 'real_time_factor', '1')
    for filename, name in [('physics', 'Physics'), ('user-commands', 'UserCommands'),
                           ('scene-broadcaster', 'SceneBroadcaster'), ('sensors', 'Sensors'),
                           ('contact', 'Contact')]:
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
    if mode == 'gripper':
        for name in ('fr3_leftfinger', 'fr3_rightfinger'):
            finger = arm.find(f"link[@name='{name}']")
            for collision in finger.findall('collision'):
                surface = collision.find('surface')
                if surface is None:
                    surface = child(collision, 'surface')
                friction = surface.find('friction')
                if friction is not None:
                    surface.remove(friction)
                ode = child(child(surface, 'friction'), 'ode')
                child(ode, 'mu', grip_friction)
                child(ode, 'mu2', grip_friction)
            sensor = child(finger, 'sensor', name=name + '_contacts', type='contact')
            child(sensor, 'always_on', 'true')
            child(sensor, 'update_rate', '1000')
            contact = child(sensor, 'contact')
            # SDFormat contact sensors accept one collision. The final stock
            # collision is the rubber gripping tip, not the screw or carriage.
            child(contact, 'collision', finger.findall('collision')[-1].get('name'))
    world.append(arm)
    if court:
        # Robot base is at the receiving baseline; court extends along +x.
        court_model = child(world, 'model', name='tennis_court')
        child(court_model, 'static', 'true')
        court_link = child(court_model, 'link', name='court')
        def box(name, xyz, size, color, collision=False):
            for kind in ('visual', 'collision') if collision else ('visual',):
                shape = child(court_link, kind, name=name)
                child(shape, 'pose', numbers(xyz) + ' 0 0 0')
                child(child(shape, 'geometry'), 'box')
                child(shape.find('geometry/box'), 'size', numbers(size))
                if kind == 'visual':
                    material = child(shape, 'material')
                    child(material, 'diffuse', color)
                    child(material, 'ambient', color)
        box('surface', [COURT_LENGTH/2, 0, .001], [COURT_LENGTH, COURT_WIDTH, .002], '.04 .18 .35 1')
        for x in (0, COURT_LENGTH, COURT_LENGTH/2 - 6.4, COURT_LENGTH/2 + 6.4):
            box('line_x_' + str(x), [x, 0, .003], [.05, COURT_WIDTH, .002], '.9 .9 .9 1')
        for y in (-COURT_WIDTH/2, COURT_WIDTH/2):
            box('line_y_' + str(y), [COURT_LENGTH/2, y, .003], [COURT_LENGTH, .05, .002], '.9 .9 .9 1')
        box('service_center', [COURT_LENGTH/2, 0, .003], [12.8, .05, .002], '.9 .9 .9 1')
        # Segments approximate the centre strap and rising net height toward
        # the posts. Collision is physical; neither launch nor control ignores it.
        for i in range(24):
            y = -COURT_WIDTH/2 + (i+.5)*COURT_WIDTH/24
            height = NET_HEIGHT + (1.07 - NET_HEIGHT)*(2*y/COURT_WIDTH)**2
            box(f'net_{i}', [COURT_LENGTH/2, y, height/2], [.02, COURT_WIDTH/24, height],
                '.12 .14 .16 .65', collision=True)
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
            child(friction, 'mu', grip_friction if mode == 'gripper' else .6)
            child(friction, 'mu2', grip_friction if mode == 'gripper' else .6)
    if mode == 'gripper':
        sensor = child(link, 'sensor', name='ball_contacts', type='contact')
        child(sensor, 'always_on', 'true')
        contact = child(sensor, 'contact')
        child(contact, 'collision', 'ball')
    # Parallel cameras looking along world +x. Optical axes: right=-y, down=-z.
    camera_x, camera_y, camera_z = camera_position
    baseline, hfov = .30, 1.35
    for side, y in [('left', baseline / 2), ('right', -baseline / 2)]:
        camera = child(world, 'model', name=side + '_camera')
        child(camera, 'static', 'true')
        child(camera, 'pose', f'{camera_x} {camera_y+y} {camera_z} 0 0 0')
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
        child(clip, 'far', '35' if court else '8')
    plugin = child(world, 'plugin', filename='libball_catching_host.so',
                   name='ball_catching::Host')
    for name, value in dict(initial_pose=numbers(initial_pose), launch_position=numbers(launch_position),
                            launch_velocity=numbers(velocity), retention=retention,
                            cup_radius=cup_radius, cup_depth=cup_depth,
                            auto_throw=str(auto_throw).lower(), trial_timeout=trial_timeout,
                            output=output, mode=mode, drag_factor=drag,
                            present=str(present).lower(), allowed_bounces=allowed_bounces,
                            court_restitution=court_restitution, court_tangent_ratio=court_tangent_ratio).items():
        child(plugin, name, value)
    path = directory / 'world.sdf'
    path.write_text(ET.tostring(sdf, encoding='unicode'))
    focal = camera_width / (2 * math.tan(hfov / 2))
    calibration = dict(focal=focal, cx=camera_width / 2, cy=camera_height / 2,
                       baseline=baseline, camera_origin=[camera_x, camera_y+baseline / 2, camera_z])
    return path, robot_path, urdf, calibration
