# Talker/listener using the universal Chimaera ROS bridge

This example has no custom bridge implementation. The unchanged `demo_nodes_cpp`
applications are launched through separate [host](src/talker_listener_bridge/launch/host.launch.py)
and [guest](src/talker_listener_bridge/launch/guest.launch.py) launch files. The shared
[topic routes](src/talker_listener_bridge/config/bridge.json) carry both directions:

```text
host talker -> /host/chatter -> universal bridge -> guest listener
guest talker -> /guest/chatter -> universal bridge -> host listener
```

The [session configuration](src/talker_listener_bridge/config/session.json) specifies
ROS domains 41/42, setup files, build jobs, install trees and simulator settings.
Keep this source configuration as the automation input: its relative paths refer
to the repository layout. Installed copies are package resources, not relocated
host build configurations.

Follow the [universal bridge guide](../chimaera_ros_ws/README.md) to bootstrap the
packages. From the repository root:

```bash
source /opt/ros/humble/setup.bash
source ros/chimaera_ros_ws/install/setup.bash
session="$PWD/ros/talker_listener_ws/src/talker_listener_bridge/config/session.json"
ros2 run chimaera_ros_bridge chimaera_ros build "$session"
# Image must be offline; this stages and installs through sudo:
ros/talker_listener_ws/deploy_guest.sh /absolute/path/disk.img 2 "$session"
ros2 launch talker_listener_bridge bringup.launch.py manifest:="$session"
```

Before deployment the image needs ROS Humble, `demo_nodes_cpp`, Python 3 and
`libjson-c5`, along with gem5 readfile boot support and passwordless root startup.
The guest starts `/opt/chimaera/session/guest_start`; the application overlay is
installed at `/opt/chimaera/session/app`. Guest output appears in gem5's serial log.

For a bounded run, edit `bridge.steps` in a copy of the session and adjust its
relative host paths, or use an absolute-path configuration. Set `simulator.image`
and `simulator.kernel` to override the defaults under `gem5/resources`.

To adapt another application, supply its host/guest launch commands and setup
files, list only the topics crossing the boundary, and configure its build/install
trees. No application source changes or Chimaera linkage are required. Services,
actions and automatic simulation clock changes are deferred.

The old example-specific bridge executables and launch timing arguments have been
replaced by the independent `chimaera_ros_bridge` package and session config. Its
ROS serialization and routing tests live with the implementation.
