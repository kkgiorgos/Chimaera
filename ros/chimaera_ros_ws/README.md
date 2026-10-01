# Universal Chimaera ROS 2 bridge

`chimaera_ros_bridge` connects **unmodified ROS 2 applications** through
`gem5-transport`. Install it independently of application workspaces. Users supply
host and guest launch files, a shared topic route JSON, and a session JSON describing
setup files, build inputs, and image paths. Existing application launch files can be
used directly; the bridge requires no application dependency or source changes.

The first version targets the existing Ubuntu 22.04/x86 ROS Humble guest. Guest
binaries must be built in an environment matching that image. It transports topics;
services and actions are outside this version. The
[wall-follow example](../wall_follow_ws/README.md) uses this bridge with
optional synchronized Gazebo Fortress stepping. The universal bridge runs
exclusively through gem5-transport.

## Build and try the example

From the repository root, in a matching build environment:

```bash
source /opt/ros/humble/setup.bash
colcon --log-base ros/chimaera_ros_ws/log build \
  --base-paths ros/chimaera_ros_ws/src ros/talker_listener_ws/src \
  --build-base ros/chimaera_ros_ws/build \
  --install-base ros/chimaera_ros_ws/install --cmake-args -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
source ros/chimaera_ros_ws/install/setup.bash
session="$PWD/ros/talker_listener_ws/src/talker_listener_bridge/config/session.json"
ros2 run chimaera_ros_bridge chimaera_ros validate "$session"
ros2 run chimaera_ros_bridge chimaera_ros plan "$session" --side host
ros2 run chimaera_ros_bridge chimaera_ros plan "$session" --side guest
```

The bridge uses generic serialized endpoints, so application message packages are
runtime dependencies on **both sides**, with matching definitions and compatible
serialization. C++ node builds require rclcpp, JSON-C, pkg-config, custom libm5,
and the sibling transport tree. `-DBUILD_GUEST_BRIDGE=OFF` permits building the host on a separate machine.
Override `GEM5_TRANSPORT_ROOT`, `GEM5_ROOT`, or `GEM5_M5_LIBRARY`
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
| `simulator` | `gem5_root`, experiment `config` path, optional `args` array and `outdir`; used by `bringup`. |
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

`bridge` transport defaults: `interval_us: 100000`, `poll_us: 10000`, `steps: 0` (unbounded),
`startup_timeout_s: 300`,
`timing_socket: /tmp/chimaera_time.sock`, `status_bar: true`, `report_seconds: 1.0`,
`max_serialized_bytes: 4096`, `max_pending_messages: 128`. Require
`0 < poll_us < interval_us <= 3600000000`. The byte limit can be raised for sensor
messages up to `67106808` bytes (64 MiB minus room for the route header); queue
capacity accepts `1..1000000`. Limits apply separately on both sides and overflow
fails explicitly. Choose bounds appropriate to guest memory; pending messages
can occupy capacity times payload size. The host status bar reports bridge traffic
and simulation progress, and is disabled when output is redirected. Intervals
advance without wall-time pacing; there is no `ratio` setting.

## Synchronized Gazebo and detailed CPUs

Build with `-DBUILD_GAZEBO_BRIDGE=ON` to install `gazebo_host_bridge`. This optional
target requires Ignition Transport 11 / Messages 8 development packages; the
ordinary `host_bridge` does not require Gazebo. Set `bridge.timing_backend` to
`gazebo` (default `gem5`), `gazebo_world` to the world name, and `physics_step_ns`
to its physics step (default 1000000). Start Gazebo paused in the host application
launch. `interval_us` must contain an integral number of physics steps.

Each interval advances gem5, delivers returned commands, allows 20ms for host
callbacks to settle, advances paused Gazebo with `multi_step`, verifies exact
iteration and simulation-time completion using world statistics, and settles
callbacks again. A service acknowledgement alone does not establish completion.
This retains the existing wall-follow boundary latency and asynchronous DDS
behavior. `bridge.timing_file` optionally records the existing benchmark timing
CSV schema, including gem5/Gazebo phase times and startup time.

Hardware and workload configuration live with each experiment. The universal
package installs the importable [chimaera_gem5.py](src/chimaera_ros_bridge/config/chimaera_gem5.py)
module, containing the timing protocol, workbegin/workend handling, ROI statistics,
and guest-startup helpers. It constructs no board, CPU, cache hierarchy or workload.

A custom gem5 config imports the module and supplies its own `Simulator`:

```python
from chimaera_gem5 import TimingServer, configure_ticks
from gem5.simulate.simulator import Simulator

configure_ticks()  # 1 THz, before constructing the experiment's board
# Construct board and configure its workload here.
timing = TimingServer(on_ready=prepare_experiment_roi)  # callback is optional
simulator = Simulator(board=board, on_exit_event=timing.exit_handlers())
timing.run(simulator, args.socket_path, managed_shutdown=args.managed_shutdown)
```

The callback runs once at the first workbegin marker, before statistics reset and
before accepting host steps. Use it for experiment-specific CPU verification and
switching. Workend and managed `QUIT` dump ROI statistics. Boot exits and intermediate
events retain the remaining step budget; reported ticks are actual gem5 progress.
The module does not start a simulation or change tick settings when imported.

`simulator.config` selects the experiment's gem5 script, relative to the session
JSON or absolute. `simulator.args` is an argument array passed unchanged, with no
shell expansion or path rewriting. CPU/cache settings, kernel/image paths, and
application parameters belong in this array and are validated by that script.
For example:

```json
"simulator": {
  "gem5_root": "/path/to/gem5",
  "config": "gem5_wall_follow.py",
  "args": ["--cpu-type", "o3", "--cpu-clock", "1GHz", "--image", "/path/to/disk.img"]
}
```

Bringup adds the installed module directory with gem5's `-p` option, then invokes
this script with `--gem5-root`, `--socket-path`, `--guest-command` and
`--managed-shutdown`. Custom configs must accept these flags; use
`add_chimaera_arguments(parser)` for the transport/startup flags and declare
`--gem5-root` in the experiment parser. These four flags cannot be overridden by
`simulator.args`. Set `bridge.m5ops` to match the experiment's ROI CPU:
`instruction` for detailed CPUs, `address` for KVM. The universal manifest does
not infer the CPU type from custom arguments.

The [wall-follow config](../wall_follow_ws/src/wall_follow_bridge/config/gem5_wall_follow.py)
owns its x86 board, CPU/cache options, SMP boot check, CPU switch, and controller
YAML injection (`--controller-file`). The
[talker/listener config](../talker_listener_ws/src/talker_listener_bridge/config/gem5_talker_listener.py)
owns its two-core KVM setup. Both use `guest_start_script()` for safe command
quoting and optional `--guest-file GUEST_PATH=HOST_PATH` injections. Injection
requires the guest destination's parent directory to exist.

See the [wall-follow session](../wall_follow_ws/src/wall_follow_bridge/config/session.json)
for synchronized settings with a 128 KiB scan limit. Existing wall-follow launch
arguments and benchmark outputs are preserved.

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
Bringup passes the generated guest startup path to the selected experiment config,
boots until workbegin, then the host controls barriers and simulation intervals.
Set `bridge.wait_for_application=true` to hold the guest's workbegin marker until
all configured local routes have application peers (an application publisher for
each outgoing route, a subscriber for each incoming route). Both examples enable
this so ROS launch/imports and DDS discovery finish on the boot CPU before the
experiment switches to detailed CPUs and resets ROI statistics. This checks topic
endpoints, not application-internal readiness or completion of every callback.
Startup is bounded by `startup_timeout_s` and stops on cancellation. The default
is false for sessions with intentionally optional or later-created routes. Guest
callbacks run after each poll; host callbacks run at interval boundaries. This does
not guarantee that arbitrary application callbacks complete within a barrier.

`run "$session" --side host` starts the bridge and host launch only; run gem5
separately using the experiment config selected by `simulator.config`. Put
`-p UNIVERSAL_SHARE/config` before the script, then pass `--gem5-root`,
`--socket-path`, `--guest-command GUEST_ROOT/guest_start`, and experiment arguments.
`plan` shows the bridge and application commands; bringup adds the gem5 command.
`run --side guest` is for **inside gem5**.
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
echo isolation, manifest validation, staged path relocation, symlink dereferencing
and process cleanup. Importable gem5 timing tests verify event handling,
remaining step budgets, drift reporting, ROI callbacks, boot failures and
startup-file quoting without booting a guest. With `BUILD_GAZEBO_BRIDGE=ON`, tests also cover 8192-beam
scans, FIFO delivery, real paused physics stepping, mismatch rejection and
cancellation using a gem5 timing stub.
`validate` and `stage` check session configuration and the full route/QoS schema
using the same C++ parser as the bridge. Topic name resolution and runtime type
support are checked when ROS endpoints are created. Actual disk deployment and gem5 boot need
an offline compatible image and KVM and are separate from these tests.
