# Universal Chimaera ROS 2 bridge

`chimaera_ros_bridge` connects **unmodified ROS 2 applications** through
`gem5-transport`. Install it independently of application workspaces. Users supply
host and guest launch files, a shared topic route JSON, and a session JSON describing
setup files, build inputs, and image paths. Existing application launch files can be
used directly; the bridge requires no application dependency or source changes.

The first version targets the existing Ubuntu 22.04/x86 ROS Humble guest. Guest
binaries must be built in an environment matching that image. It transports topics;
services/actions and automatic `/clock` or `use_sim_time` changes are outside this
version. The wall-follow integration continues to use its existing bridge.

## Build and try the example

From the repository root, in a matching build environment:

```bash
source /opt/ros/humble/setup.bash
colcon --log-base ros/chimaera_ros_ws/log build \
  --base-paths ros/chimaera_ros_ws/src ros/talker_listener_ws/src \
  --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install --cmake-args -DBUILD_TESTING=ON
source ros/chimaera_ros_ws/install/setup.bash
session="$PWD/ros/talker_listener_ws/src/talker_listener_bridge/config/session.json"
ros2 run chimaera_ros_bridge chimaera_ros validate "$session"
ros2 run chimaera_ros_bridge chimaera_ros plan "$session" --side host
ros2 run chimaera_ros_bridge chimaera_ros plan "$session" --side guest
```

The bridge uses generic serialized endpoints, so application message packages are
runtime dependencies on **both sides**, with matching definitions and compatible
serialization. C++ node builds require rclcpp, JSON-C, pkg-config, custom libm5,
and the sibling transport tree. `-DBUILD_GUEST_BRIDGE=OFF` permits host-only builds;
`-DBUILD_MOCK_BRIDGE_TEST=OFF` disables the mock-sim integration test.
Override `GEM5_TRANSPORT_ROOT`, `GEM5_ROOT`, `GEM5_M5_LIBRARY`, or `MOCK_SIM_ROOT`
with CMake arguments when the trees live elsewhere.

## Configuration contract

See the [example session](../talker_listener_ws/src/talker_listener_bridge/config/session.json)
and [routes](../talker_listener_ws/src/talker_listener_bridge/config/bridge.json).
The session has `version: 1` and these fields:

| Field | Meaning |
| --- | --- |
| `routes` | Shared route JSON path, relative to the session file or absolute. |
| `host`, `guest` | Distinct `domain_id` values, `setup` arrays, optional `env`, and `processes` arrays. |
| `processes` | Entries with a unique `name` and nonempty `command` argument array. Typically `ros2 launch PACKAGE host.launch.py` and its guest equivalent. Arguments are preserved without shell expansion. |
| `bridge` | Optional timing, status, and buffer parameters described below. |
| `simulator` | `gem5_root`, optional `image`, `kernel`, `outdir`; used by `bringup`. |
| `builds` | Build jobs with `sources`, `output`, `setup`, and optional `colcon_args`. |
| `deploy` | Absolute `guest_root` (default `/opt/chimaera/session`), and `install_trees` with host `source` and relative guest `destination`. |

Host paths are relative to the session file. Guest setup paths are absolute **inside
the image**, typically `/opt/ros/humble/setup.bash` followed by a deployed overlay's
`local_setup.bash`. Use `local_setup.bash` for copied install trees so generated
`setup.bash` does not source build-time host underlay paths. Application file paths
passed as command arguments must be valid on their respective side; the runner
does not rewrite launch arguments, parameter files, model paths, or application
resources. All required non-system assets should be installed by the application.

Each side automatically starts its bridge, then the configured application commands.
Separate localhost-only ROS domains isolate DDS discovery. Domain variables are
applied after sourcing overlays and cannot be overridden in `env`. Other environment
variables can be supplied, including application-specific resource paths. Startup
scripts are trusted code; use only trusted configuration and install trees.

A route requires `host_topic`, `guest_topic`, `type`, `direction` (`host_to_guest` or
`guest_to_host`) and explicit `qos` (`history: keep_last`, `depth: 1..128`,
`reliability: reliable|best_effort`, `durability: volatile|transient_local`). Each
absolute local topic can occur only once per side. Route identity includes both
topics, direction, type and QoS, independent of JSON ordering. Unknown incoming
routes or mismatches fail the bridge. Remappings that merge local bridge topics
are rejected to prevent loops. There is no startup route handshake; unused route
mismatches are detected only when messages arrive. Restart both sides after edits.

QoS governs each local ROS hop, not end-to-end DDS identity or acknowledgments.
Transient-local cache belongs to the bridge publisher and is lost on restart.
Deadline, lifespan and custom liveliness policies are unsupported. No intra-process
or loaned-message transfer is provided. Applications must offer/request compatible
QoS on the local hop.

`bridge` defaults: `interval_us: 100000`, `poll_us: 10000`, `steps: 0` (unbounded),
`ratio: 1.0` (simulation seconds per wall second), `startup_timeout_s: 300`,
`timing_socket: /tmp/chimaera_time.sock`, `status_bar: true`, `report_seconds: 1.0`,
`max_serialized_bytes: 4096`, `max_pending_messages: 128`. Require
`0 < poll_us < interval_us <= 3600000000`. The byte limit can be raised for sensor
messages up to `67106808` bytes (64 MiB minus room for the route header); queue
capacity accepts `1..1000000`. Limits apply separately on both sides and overflow
fails explicitly. Choose bounds appropriate to guest memory; pending messages
can occupy capacity times payload size. The host status bar reports bridge traffic
and pacing, and is disabled when output is redirected.

## Build, stage, deploy, run

After bootstrapping the universal package above, configured application builds run
with:

```bash
ros2 run chimaera_ros_bridge chimaera_ros build "$session"
```

Each build invokes colcon with source, build, install and log directories from the
session, after sourcing its setup files. It does not edit source workspaces. System
dependencies (ROS, demo_nodes_cpp, message support, JSON-C runtime, Python and any
application libraries) must already be installed in the image and build environment.
This version does not install apt/rosdep dependencies or cross-compile. Prefer regular
colcon installs; staging dereferences symlink installs and fails on missing targets.

Stage a new directory, review its contents, then deploy to an **offline** image:

```bash
ros2 run chimaera_ros_bridge chimaera_ros stage "$session" --output /tmp/my-guest-root
ros2 run chimaera_ros_bridge chimaera_ros deploy "$session" \
  --output /tmp/my-guest-root --image /absolute/path/disk.img --partition 2
ros2 run chimaera_ros_bridge chimaera_ros bringup "$session"
# Equivalent launch entry point:
ros2 launch chimaera_ros_bridge session.launch.py manifest:="$session"
```

Staging copies the guest executable, Python supervisor, shared routes, relocated
session config and configured install trees. It generates `GUEST_ROOT/guest_start`.
The installer mounts the image once with sudo, serializes deployments, rejects
already attached loop images and destination symlinks, and cleans up mounts/devices
on exit. Stop gem5/QEMU before deployment: a file lock cannot prove another VM is
not using the image. Existing files in the staged paths are replaced; deployment
is not transactional and does not remove obsolete application files.

The image must execute gem5 readfile on boot and permit passwordless root startup
(or already run readfile as root), because the guest bridge maps m5 memory.
Bringup passes the generated guest startup path to the universal gem5 configuration,
boots until workbegin, then the host controls barriers and wall-clock pacing. Guest
callbacks run after each poll; host callbacks run at interval boundaries. This does
not guarantee that arbitrary application callbacks complete within a barrier.

`run "$session" --side host` starts the bridge and host launch only; run gem5
separately using the installed `config/gem5_ros.py` with `--gem5-root` and
`--guest-command GUEST_ROOT/guest_start`. `run --side guest` is for **inside gem5**.
The staged startup script selects its staged executable without ROS package lookup.

When a supervised command exits, the runner stops all process groups. Ctrl+C does
the same, with a ten-second grace period before forced cleanup. An existing launch
file controls its own node lifecycle; add exit handlers there if any node exit
should stop that application launch. Only one transport session can run at a time
because gem5 data socket paths are fixed. Existing sockets are never unlinked at
startup; remove stale sockets only after checking no process owns them.

## Validation

```bash
colcon --log-base ros/chimaera_ros_ws/log test \
  --base-paths ros/chimaera_ros_ws/src ros/talker_listener_ws/src \
  --build-base ros/chimaera_ros_ws/build --install-base ros/chimaera_ros_ws/install \
  --event-handlers console_direct+
colcon --log-base ros/chimaera_ros_ws/log test-result --test-result-base ros/chimaera_ros_ws/build --verbose
```

Tests cover route validation, ROS serialization/QoS and limits, malformed frames,
echo isolation, real external talkers/listeners through mock-sim barriers, manifest
validation, staged path relocation, symlink dereferencing and process cleanup.
`validate` and `stage` check session configuration and the full route/QoS schema
using the same C++ parser as the bridge. Topic name resolution and runtime type
support are checked when ROS endpoints are created. Actual disk deployment and gem5 boot need
an offline compatible image and KVM and are separate from these tests.
