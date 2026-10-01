from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch_ros.actions import Node


def generate_launch_description():
    nodes = [
        Node(package='demo_nodes_cpp', executable='talker', name='guest_talker',
             remappings=[('chatter', '/guest/chatter')], output='screen'),
        Node(package='demo_nodes_cpp', executable='listener', name='guest_listener',
             remappings=[('chatter', '/host/chatter')], output='screen'),
    ]
    return LaunchDescription([
        *[RegisterEventHandler(OnProcessExit(target_action=node,
           on_exit=[EmitEvent(event=Shutdown(reason='application node exited'))])) for node in nodes],
        *nodes,
    ])
