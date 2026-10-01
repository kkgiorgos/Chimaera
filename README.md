# Chimaera

Chimaera is a research project that connects **computer simulation** with
**robot simulation**. It runs robot software inside gem5, which simulates the
computer executing that software, while Gazebo simulates the robot and its
environment. The aim is to study how computer hardware and software choices
affect robot behavior.

The main example is a robot that follows a wall using laser scans.

## How it fits together

- **Host**: your real Linux machine, running Gazebo, experiment recording, and
  the coordination code.
- **Guest**: a Linux system inside gem5, running ROS 2 and the robot controller.
- **Bridge**: passes sensor readings, simulation time, and movement commands
  between the host and guest.

```text
Gazebo → laser scans and clock → gem5 guest controller
Gazebo ← movement commands    ← gem5 guest controller
```

Chimaera advances gem5 and Gazebo in coordinated, sequential intervals and
exchanges messages at their boundaries. In the wall-follow example, KVM
(hardware-assisted virtualization) speeds up guest boot; execution then switches
to a simulated CPU so experiments can collect CPU and cache statistics.
Simulation time and the real time spent running an experiment are different.

## Where to look

| Directory | Purpose |
| --- | --- |
| [ros/wall_follow_ws](ros/wall_follow_ws/README.md) | Main robot example, setup, experiment sweeps, plots, and dashboards. |
| [ros/chimaera_ros_ws](ros/chimaera_ros_ws/README.md) | Universal ROS topic bridge, application builds, guest deployment, and host/guest orchestration. |
| [ros/talker_listener_ws](ros/talker_listener_ws/README.md) | Smaller example sending ROS messages between host and guest. |
| [gem5-transport](gem5-transport/README.md) | C++ message transport; [controllers](gem5-transport/CONTROLLERS.md) coordinate guest execution. |
| `gem5/` | Modified gem5 source, including Chimaera's guest-to-host operations. |
| `docs/` | Architecture material, [custom gem5 operations](docs/gem5ops.md), and an [efficiency audit](docs/gem5-cosimulation-efficiency-audit.md). |
| [legacy/gem5-transport-demos](legacy/gem5-transport-demos/README.md) | Frozen raw transport and controller demos with their build dependencies. |
| [legacy/mock-sim](legacy/mock-sim/README.md), [legacy/queue-manager](legacy/queue-manager/README.md) | Archived local transport demos and standalone queues. |
| `legacy/`, `ros/franka_ws/` | Earlier implementations and additional robot integration work. |

## Start here

For a small gem5 example, follow the [talker/listener guide](ros/talker_listener_ws/README.md).
The [universal bridge guide](ros/chimaera_ros_ws/README.md) covers building the
bridge, staging applications, and running host/guest sessions through gem5-transport.

For the full robot experiment, follow the [wall-follow guide](ros/wall_follow_ws/README.md).
It covers ROS 2 Humble, Gazebo Fortress, custom gem5/libm5, the guest kernel and
disk image, deployment, and running a suite. Build and deploy the guest software
before launching a simulation. Each suite retains configuration, logs, raw
measurements, and summaries for comparison.
