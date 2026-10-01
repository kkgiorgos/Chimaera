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
| `docs/` | Architecture material and [custom gem5 operations](docs/gem5ops.md). |
| `ros/franka_ws/` | Additional robot integration work. |

Historical components are preserved under `legacy/` on the `archive` branch.
Use `git switch archive` to access them; active development continues on `main`.

## Start here

**A fresh clone is not ready to run a gem5 session.** The repository supplies
source and example configurations, but not the built simulator, built ROS
workspaces, prepared guest disk image, or guest kernel. Complete the following
setup before following an example's launch commands.

### Host prerequisites

The supplied ROS examples target **x86-64 Linux with Ubuntu 22.04 and ROS 2
Humble**, matching the guest. You need:

| Requirement | Used for |
| --- | --- |
| Hardware virtualization enabled in firmware, Linux KVM, and read/write access to `/dev/kvm` | Both supplied gem5 configurations require KVM. Wall-follow uses it for boot; talker/listener stays on KVM. A host VM must expose nested virtualization. |
| C++20 compiler, Make, CMake **3.22+**, pkg-config, Python **3.10+**, SCons, Python development headers, zlib development files, and m4 | Building the simulator, transport, and ROS packages. The repository's [gem5 dependency Dockerfile](gem5/util/dockerfiles/ubuntu-22.04_all-dependencies/Dockerfile) lists additional optional gem5 libraries. |
| ROS 2 Humble, its development tools (`colcon`, `ament_cmake`, `ament_cmake_python`), and JSON-C development files (`libjson-c-dev`) | Building and running the ROS bridges. Talker/listener also requires `demo_nodes_cpp`. |
| Gazebo **Fortress**, `ros_gz_sim`, `ros_gz_bridge`, Ignition Transport **11** and Messages **8** development files | Wall-follow simulation and its timing controller. These are not needed for talker/listener. |
| NumPy, Matplotlib, PyYAML, and ROS `rclpy` | Wall-follow analysis and host recording. Install the analysis package with `python3 -m pip install -e ros/wall_follow_ws/benchmarking`. |
| Bash, sudo, Python 3, and Linux image tools (`losetup`, `mount`, `umount`, `flock`, `realpath`, `install`, `sync`) | Offline guest deployment. Loop devices and mounting privileges must be available; these tools are normally provided by util-linux and coreutils. |

Install dependencies for the workspace you intend to use, including its
`package.xml` dependencies. The build and deployment commands do **not** install
host or guest system packages. Source `/opt/ros/humble/setup.bash` and the built
workspace's `install/setup.bash` in each Bash terminal running ROS commands;
use the corresponding `.zsh` files in zsh. The universal session runner itself
uses Bash setup files.

Check KVM access as the user who will run gem5:

```bash
test -r /dev/kvm && test -w /dev/kvm
```

If this fails, enable virtualization/load the appropriate KVM module and arrange
device access (commonly membership in the `kvm` group, followed by a new login).
Allow resources for the guest's configured 3 GiB of RAM as well as gem5, ROS,
Gazebo, build outputs, the disk image, and experiment results.

### Build the supplied custom gem5 and libm5

From the repository root, after installing the build dependencies:

```bash
cd gem5
scons build/X86/gem5.opt -j2
cd util/m5
scons build/x86/out/m5 -j2
cd ../../..
```

Adjust parallelism to available memory. The examples expect
`gem5/build/X86/gem5.opt` and `gem5/util/m5/build/x86/out/libm5.a`.
Use this modified source tree: an upstream gem5/libm5 build does not supply
Chimaera's custom operations. The supplied X86 build selects the
`MESI_Two_Level` protocol required by the example configurations and must include
KVM support. ROS bridge builds include `gem5-transport` automatically; a separate
`make -C gem5-transport` is only needed when using its libraries directly.

### Supply and prepare the guest image and kernel

You must obtain or create these **untracked external artifacts**:

| Default path from the repository root | Required contents |
| --- | --- |
| `gem5/resources/x86-ubuntu-22.04-ros-humble.img` | A raw, partitioned x86-64 Ubuntu 22.04 disk image with ROS Humble and the guest dependencies below. |
| `gem5/resources/x86-linux-kernel-5.15.180` | A gem5-compatible x86 Linux kernel that can boot the image. |

These filenames are defaults, not automatic downloads. There is currently no
complete image provisioning script or published prepared-image download in this
repository. Preparing an image using QEMU requires QEMU and image-resizing/
network tools.

Prepare the image with all of the following before deploying:

- ROS Humble at `/opt/ros/humble`, Python 3, and JSON-C runtime (`libjson-c5`).
  Talker/listener needs `demo_nodes_cpp`; wall-follow needs `sensor_msgs`,
  `geometry_msgs`, `rosgraph_msgs`, and `rcl_interfaces`. Other applications need
  their libraries and message type support installed on both host and guest,
  with matching message definitions.
- A boot hook that reads gem5's supplied script using `m5 readfile` and executes
  it with Bash. Merely installing the `m5` binary is not enough. The supplied
  boot scripts invoke `sudo -n`, so configure passwordless guest sudo for the
  boot user, or run the boot hook as root with working noninteractive sudo.
  Guest bridges need permission to map m5 memory through `/dev/mem` or
  `/dev/gem5_bridge`.
- A root filesystem on **partition 2**, bootable as `/dev/sda2`. Both example
  gem5 configs hard-code `root=/dev/sda2`; changing the deployment partition
  alone does not change the boot partition. Other layouts require editing the
  gem5 config too.

Build guest binaries in an environment matching the image's architecture, ROS,
and C/C++ runtime. These scripts copy native builds; they do not cross-compile
or install missing shared libraries in the image. A matching Ubuntu 22.04 build
environment is especially important when the host uses a newer distribution.

You can keep the artifacts elsewhere: add `--image PATH` and
`--kernel PATH` to `simulator.args` in the talker/listener session JSON, or pass `image:=...` and
`kernel:=...` to wall-follow bringup (`--image`/`--kernel` for its suite runner).

### Build, deploy, then launch an example

1. Build and source the chosen ROS workspace using the linked guide below.
   Build the guest bridge too; `-DBUILD_GUEST_BRIDGE=OFF` is only for host-only
   builds, with guest binaries built separately.
2. Stop any gem5/QEMU process using the image, then deploy the built guest
   executables, configuration, and startup script. Host sudo for mounting the
   image and guest passwordless sudo for boot are separate requirements.
3. For wall-follow, generate a world and controller YAML as shown in its guide.
4. Launch on the same host as gem5, with access to the same `/tmp` Unix sockets.
   Run one Chimaera session at a time: data socket paths are fixed. If launching
   manually, start the host bridge before gem5. After a forced shutdown, check
   socket ownership with `ss -xapn` before removing stale sockets.

For a small gem5 example, follow the [talker/listener guide](ros/talker_listener_ws/README.md).
The [universal bridge guide](ros/chimaera_ros_ws/README.md) covers building the
bridge, staging applications, and running host/guest sessions through gem5-transport.

For the full robot experiment, follow the [wall-follow guide](ros/wall_follow_ws/README.md).
It covers ROS 2 Humble, Gazebo Fortress, custom gem5/libm5, the guest kernel and
disk image, deployment, and running a suite. Build and deploy the guest software
before launching a simulation. Each suite retains configuration, logs, raw
measurements, and summaries for comparison.

For a local wall-follow benchmark without gem5, the guest image, kernel, KVM,
libm5, and deployment are unnecessary. Build only `wall_follow_robot` and
`wall_follow_benchmark` so colcon does not also build the gem5 bridge. Analysis
alone needs only Python and the analysis package. The optional Franka workspace
has its own [dependency and Docker setup](ros/franka_ws/src/README.md), including
external repositories; it is not required for either main example.
