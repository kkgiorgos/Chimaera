"""Generate a self-contained Fortress arena. Configurable rectangular inner dimensions."""
import math


def box(name, x, y, sx, sy):
    return f'''<model name="{name}"><static>true</static><pose>{x} {y} 0.6 0 0 0</pose>
    <link name="link"><collision name="collision"><geometry><box><size>{sx} {sy} 1.2</size></box></geometry></collision>
    <visual name="visual"><geometry><box><size>{sx} {sy} 1.2</size></box></geometry>
    <material><ambient>0.6 0.7 0.8 1</ambient></material></visual></link></model>'''


def validate_arena(arena_width, arena_height):
    if any(not math.isfinite(v) or v < 4.0 for v in (arena_width, arena_height)):
        raise ValueError('arena_width and arena_height must be finite and at least 4 metres')


def wall_distance(x, y, arena_width, arena_height):
    return min(arena_width/2-abs(x), arena_height/2-abs(y))


def make_world(lidar_hz=20., lidar_samples=720, noise_std=0., physics_step=0.001,
               arena_width=12., arena_height=8.):
    validate_arena(arena_width, arena_height)
    hx, hy = arena_width/2, arena_height/2
    if not (math.isfinite(lidar_hz) and 0 < lidar_hz <= 500 and 32 <= lidar_samples <= 8192
            and math.isfinite(noise_std) and noise_std >= 0 and 0 < physics_step <= 0.01):
        raise ValueError('Invalid sensor or physics configuration')
    walls = ''.join([box('south',0,-hy-.1,arena_width+.4,.2), box('north',0,hy+.1,arena_width+.4,.2),
                     box('east',hx+.1,0,.2,arena_height), box('west',-hx-.1,0,.2,arena_height)])
    wheels = ''
    for name, y in [('left',.22),('right',-.22)]:
        wheels += f'''<link name="{name}"><pose>0 {y} 0.10 -1.57079632679 0 0</pose>
        <inertial><mass>0.3</mass><inertia><ixx>.001</ixx><iyy>.001</iyy><izz>.0015</izz></inertia></inertial>
        <collision name="c"><geometry><cylinder><radius>.1</radius><length>.04</length></cylinder></geometry></collision>
        <visual name="v"><geometry><cylinder><radius>.1</radius><length>.04</length></cylinder></geometry></visual></link>
        <joint name="{name}_joint" type="revolute"><parent>base</parent><child>{name}</child>
        <axis><xyz expressed_in="__model__">0 1 0</xyz><limit><lower>-1e16</lower><upper>1e16</upper></limit></axis></joint>'''
    return f'''<?xml version="1.0"?><sdf version="1.8"><world name="wall_arena">
    <physics name="physics" type="ignored"><max_step_size>{physics_step}</max_step_size><real_time_factor>1</real_time_factor></physics>
    <plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
    <plugin filename="ignition-gazebo-user-commands-system" name="ignition::gazebo::systems::UserCommands"/>
    <plugin filename="ignition-gazebo-scene-broadcaster-system" name="ignition::gazebo::systems::SceneBroadcaster"/>
    <plugin filename="ignition-gazebo-sensors-system" name="ignition::gazebo::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
    <light name="sun" type="directional"><pose>0 0 10 0 0 0</pose><diffuse>.8 .8 .8 1</diffuse><direction>-.5 .1 -.9</direction></light>
    <model name="ground"><static>true</static><link name="ground"><collision name="c"><geometry><plane><normal>0 0 1</normal><size>{max(100,arena_width+10)} {max(100,arena_height+10)}</size></plane></geometry></collision>
    <visual name="v"><geometry><plane><normal>0 0 1</normal><size>{max(100,arena_width+10)} {max(100,arena_height+10)}</size></plane></geometry></visual></link></model>
    {walls}<model name="robot"><pose>{-hx+min(2.,arena_width/4)} {-hy+1.} 0 0 0 0</pose>
    <link name="base"><pose>0 0 .17 0 0 0</pose><inertial><mass>3</mass><inertia><ixx>.04</ixx><iyy>.05</iyy><izz>.07</izz></inertia></inertial>
    <collision name="c"><geometry><box><size>.42 .36 .16</size></box></geometry></collision>
    <visual name="v"><geometry><box><size>.42 .36 .16</size></box></geometry><material><ambient>.1 .4 .9 1</ambient></material></visual>
    <sensor name="lidar" type="gpu_lidar"><pose>0 0 .15 0 0 0</pose><topic>/scan</topic><update_rate>{lidar_hz}</update_rate>
    <lidar><scan><horizontal><samples>{lidar_samples}</samples><resolution>1</resolution><min_angle>-3.14159265359</min_angle><max_angle>3.14159265359</max_angle></horizontal></scan>
    <range><min>.05</min><max>20</max><resolution>.001</resolution></range>
    <noise><type>gaussian</type><mean>0</mean><stddev>{noise_std}</stddev></noise></lidar></sensor></link>
    {wheels}<link name="caster"><pose>-.16 0 .05 0 0 0</pose>
    <inertial><mass>.1</mass><inertia><ixx>.0001</ixx><iyy>.0001</iyy><izz>.0001</izz></inertia></inertial>
    <collision name="c"><geometry><sphere><radius>.05</radius></sphere></geometry><surface><friction><ode><mu>0</mu><mu2>0</mu2></ode></friction></surface></collision>
    <visual name="v"><geometry><sphere><radius>.05</radius></sphere></geometry></visual></link>
    <joint name="caster_joint" type="fixed"><parent>base</parent><child>caster</child></joint><link name="front_caster"><pose>.16 0 .05 0 0 0</pose>
    <inertial><mass>.1</mass><inertia><ixx>.0001</ixx><iyy>.0001</iyy><izz>.0001</izz></inertia></inertial>
    <collision name="c"><geometry><sphere><radius>.05</radius></sphere></geometry><surface><friction><ode><mu>0</mu><mu2>0</mu2></ode></friction></surface></collision>
    <visual name="v"><geometry><sphere><radius>.05</radius></sphere></geometry></visual></link>
    <joint name="front_caster_joint" type="fixed"><parent>base</parent><child>front_caster</child></joint>
    <plugin filename="ignition-gazebo-diff-drive-system" name="ignition::gazebo::systems::DiffDrive">
    <left_joint>left_joint</left_joint><right_joint>right_joint</right_joint><wheel_separation>.44</wheel_separation><wheel_radius>.1</wheel_radius>
    <topic>/cmd_vel</topic><odom_topic>/wheel_odom</odom_topic><odom_publish_frequency>50</odom_publish_frequency>
    <max_linear_acceleration>1</max_linear_acceleration><min_linear_acceleration>-1</min_linear_acceleration>
    <max_angular_acceleration>3</max_angular_acceleration><min_angular_acceleration>-3</min_angular_acceleration></plugin>
    <plugin filename="ignition-gazebo-odometry-publisher-system" name="ignition::gazebo::systems::OdometryPublisher">
    <dimensions>2</dimensions><odom_publish_frequency>50</odom_publish_frequency><odom_topic>/ground_truth</odom_topic></plugin>
    </model></world></sdf>'''
