"""Traffic shapes taken from the ball-catching application and bridge session."""
import math

BALL_DEFAULT = dict(camera_width=640, camera_height=480, camera_hz=90.,
                    joint_hz=250., control_hz=250., image_age_periods=2.)


def traffic(config):
    if config.get('workload', 'synthetic') != 'ball_catching':
        return []
    settings = dict(BALL_DEFAULT, **config.get('ball_catching', {}))
    return [dict(id=1, name='left_image', label='Left camera', topic='/stereo/left/image_raw',
                 type='sensor_msgs/msg/Image', direction='host_to_guest',
                 rate_hz=settings['camera_hz'], body_bytes=settings['camera_width']*settings['camera_height']*3,
                 depth=2, coalesce=True, coalesce_group='stereo'),
            dict(id=2, name='right_image', label='Right camera', topic='/stereo/right/image_raw',
                 type='sensor_msgs/msg/Image', direction='host_to_guest',
                 rate_hz=settings['camera_hz'], body_bytes=settings['camera_width']*settings['camera_height']*3,
                 depth=2, coalesce=True, coalesce_group='stereo'),
            dict(id=3, name='joint_states', label='Joint feedback', topic='/robot/joint_states',
                 type='sensor_msgs/msg/JointState', direction='host_to_guest',
                 rate_hz=settings['joint_hz'], depth=1, coalesce=False),
            dict(id=4, name='effort_command', label='Effort command', topic='/robot/effort_command',
                 type='sensor_msgs/msg/JointState', direction='guest_to_host',
                 rate_hz=settings['control_hz'], depth=1, coalesce=False),
            dict(id=5, name='clock', label='ROS clock', topic='/clock',
                 type='rosgraph_msgs/msg/Clock', direction='host_to_guest',
                 rate_hz=1e9/config['physics_step_ns'], depth=1, coalesce=True)]


def ball_routes(config):
    topics = []
    for stream in traffic(config) + [
            dict(name='ready', topic='/robot/ready', type='std_msgs/msg/Bool',
                 direction='guest_to_host', depth=1, coalesce=False),
            dict(name='perception_ready', topic='/robot/perception_ready', type='std_msgs/msg/Bool',
                 direction='guest_to_host', depth=1, coalesce=False)]:
        root = '/latency/ball/' + stream['name']
        route = dict(host_topic=root+'/host', guest_topic=root+'/guest',
            type=stream['type'], direction=stream['direction'], coalesce=stream['coalesce'],
            qos=dict(history='keep_last', depth=stream['depth'], durability='volatile',
                     reliability='reliable' if stream['type']=='std_msgs/msg/Bool' else 'best_effort'))
        if stream.get('coalesce_group'):
            route['coalesce_group'] = stream['coalesce_group']
        topics.append(route)
    return dict(version=1, topics=topics)


def image_age(config):
    settings = dict(BALL_DEFAULT, **config.get('ball_catching', {}))
    return math.ceil(settings['image_age_periods']/settings['camera_hz']*1e9)
