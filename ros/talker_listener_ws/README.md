# Talker–listener across gem5

Two C++ support nodes connect the standard ROS 2 `demo_nodes_cpp` talkers and
listeners through Chimaera's sibling `gem5-transport` project. Both directions
run together:

```text
host talker  -> /host/chatter -> host_bridge -> gem5 -> guest_bridge -> guest listener
guest talker -> /guest/chatter -> guest_bridge -> gem5 -> host_bridge -> host listener
```

The host runs ROS domain 41 and the guest domain 42, both localhost-only.
Both bridges read the same JSON route configuration and automatically create ROS
subscriptions for outgoing routes and publishers for incoming routes. The supplied
[`bridge.json`](src/talker_listener_bridge/config/bridge.json) preserves the two
external `demo_nodes_cpp` talker/listener pairs shown above.

Messages use ROS serialized payloads with a versioned route header. Message type
support must be installed and sourced on **both** machines, with matching message
definitions and compatible ROS serialization. There are at most 128 pending
outgoing messages across all routes, each at most 4096 **serialized payload** bytes
(excluding the route header). For `std_msgs/msg/String`, serialization uses nine
additional bytes, leaving 4087 bytes for text. Exceeding either limit fails the
bridge instead of silently dropping its transport queue. The wire format has
changed; rebuild and redeploy both bridges together.

## JSON configuration

A configuration contains `version: 1` and a nonempty `topics` array. Each entry
requires these fields (no implicit QoS defaults):

```json
{
  "version": 1,
  "topics": [
    {
      "host_topic": "/sensors/temperature",
      "guest_topic": "/input/temperature",
      "type": "std_msgs/msg/Float64",
      "direction": "host_to_guest",
      "qos": {
        "history": "keep_last",
        "depth": 10,
        "reliability": "best_effort",
        "durability": "volatile"
      }
    }
  ]
}
```

`host_to_guest` subscribes on the host and publishes on the guest;
`guest_to_host` reverses those roles. Topic names may differ between sides and
must be absolute. For traffic in both directions, use two routes with separate
local topics, as in the default config. Each local topic may occur only once;
remappings that merge routes are rejected to prevent feedback. External nodes
remain independent of configuration: when changing the demo topics, also adjust
the external nodes' remappings in `host.launch.py` and `guest_start.sh`.

The maximum is 128 routes. Unknown fields, unsupported QoS values, invalid depths,
and missing required fields fail startup. Configuration is loaded once at startup;
restart both bridges after changes. Route ordering and JSON formatting need not
match. Every frame identifies the complete route (names, direction, type, and QoS);
unrecognized or mismatched incoming routes fail rather than reaching the wrong
publisher. There is no startup configuration handshake: differences on unused
routes are not detected until traffic arrives.

Choose a host config with either launch file:

```bash
ros2 launch talker_listener_bridge bringup.launch.py \
  gem5_root:="$(realpath ../../gem5)" config_file:=/absolute/path/bridge.json
# Or start just the bridge:
ros2 run talker_listener_bridge host_bridge --ros-args \
  -p config_file:=/absolute/path/bridge.json
```

Without `config_file`, a bridge uses the installed package's `config/bridge.json`.
The deployed guest startup script explicitly uses
`/usr/local/share/chimaera/bridge.json`; set `CHIMAERA_BRIDGE_CONFIG` inside the guest
to override it. Host file paths are not automatically shared with the guest.
Deploy the same JSON to the offline guest image (third positional argument):

```bash
./deploy_guest.sh /absolute/path/to/disk.img 2 /absolute/path/bridge.json
```

## QoS support and limitations

The configured QoS is applied to both ROS endpoints of each route. It describes
local DDS behavior; Chimaera carries message bytes across a synchronization
barrier, not the original DDS writer identity, acknowledgments, or QoS events.

| Setting | Accepted values | Limitation |
| --- | --- | --- |
| History | `keep_last` | Only bounded history; `keep_all` is rejected. |
| Depth | Integer 1–128 | ROS endpoint history depth; the separate transport queue has a global 128-message limit and fails on overflow. |
| Reliability | `reliable`, `best_effort` | Enforced on each local ROS hop. Reliable does not add end-to-end acknowledgments, restart recovery, or exactly-once delivery. Best-effort samples may be lost on either ROS hop. |
| Durability | `volatile`, `transient_local` | Transient-local caching is owned by each local DDS publisher, including the bridge publisher. Cache contents are not persisted through bridge restarts; history older than what the bridge received cannot be reconstructed. |

Deadline, lifespan, manual liveliness, liveliness lease duration, system-default
policies, and additional QoS keys are rejected. The bridge uses automatic local
DDS liveliness and default/infinite timing policies. It cannot propagate the
source writer's liveliness or deadline events. Lifespan would require carrying
source timestamps and translating host wall time to guest simulation time;
resetting age at republish would not preserve its meaning. Pausing the guest and
batching at barriers also prevent end-to-end real-time deadline guarantees.

External publishers/subscribers must offer/request compatible QoS. In particular,
a reliable bridge subscription will not match a best-effort source, and a
transient-local subscription requires a transient-local source. The default demo
uses reliable, volatile endpoints. Generic serialized endpoints do not support
intra-process or loaned-message/zero-copy transfer through this bridge. Services,
actions, and automatic same-topic bidirectional loop suppression are not provided.

The host bridge owns `Gem5HostController`, its transport worker and the timing
controller. It advances gem5 at synchronization boundaries and paces simulated
time against wall time. The guest bridge owns `Gem5GuestController`, links the
custom **libm5.a**, maps m5 memory, and issues the workbegin marker before polling.
ROS callbacks and controller calls share one thread in each bridge. Applications
push outgoing data with `submit()` and pull incoming data with `take()`. The guest
pumps ROS callbacks once after each successful poll performed by `run_next()`,
after publishing received frames and before queuing outgoing frames for the next
poll. Bridge `take()` only pops queued data.
The console/channel-service examples are not needed for this application: the
support nodes are the controller producers and consumers themselves.

## Build

From this workspace, on Ubuntu 22.04 with ROS Humble, `demo_nodes_cpp`, colcon,
`libjson-c-dev`, `pkg-config`, CMake >= 3.22 and the custom x86 gem5/libm5 build available:

```bash
source /opt/ros/humble/setup.bash
colcon build --cmake-args -DBUILD_TESTING=ON
source install/setup.bash
colcon test --event-handlers console_direct+
colcon test-result --verbose
```

Every build generates `build/talker_listener_bridge/compile_commands.json`,
including the bridge nodes and transport sources. For clangd discovery from the
workspace root, create this symlink once after building:

```bash
ln -s build/talker_listener_bridge/compile_commands.json compile_commands.json
```

The symlink stays current across rebuilds; compilation databases are ignored by Git.

By default the package finds `../../gem5-transport` relative to this workspace,
and the transport finds its sibling `gem5` and `queue-manager` trees. Overrides:

```bash
colcon build --cmake-args \
  -DGEM5_TRANSPORT_ROOT=/absolute/path/to/gem5-transport \
  -DGEM5_ROOT=/absolute/path/to/gem5 \
  -DGEM5_M5_LIBRARY=/absolute/path/to/libm5.a
```

`-DBUILD_GUEST_BRIDGE=OFF` builds only the host node without libm5. Build the
guest executable against the guest OS's architecture and runtime; the supplied
image is x86 Ubuntu 22.04 / ROS Humble. The guest target links libm5 explicitly
and inherits `-no-pie` from the transport.

## Deploy once, with the image offline

Stop gem5/QEMU before editing the image. This installs the guest executable and
its startup script and JSON config using the existing transport image installer (requires sudo):

```bash
./deploy_guest.sh
# Optional image and partition:
./deploy_guest.sh /absolute/path/to/disk.img 2
```

The image must already contain ROS Humble, `demo_nodes_cpp`, and the JSON-C runtime
(`libjson-c5` on Ubuntu 22.04), execute the gem5
readfile script on boot, and permit that script to run the guest startup command
as root via passwordless sudo (or already run as root). Root is needed for the
m5 mapping device or `/dev/mem`. The startup script is installed at
`/usr/local/bin/chimaera_talker_listener_guest`; its bridge executable is at
`/usr/local/bin/chimaera_guest_bridge`. Run these **inside gem5 only**.

## Automatic bringup

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch talker_listener_bridge bringup.launch.py \
  gem5_root:="$(realpath ../../gem5)"
```

This starts the host talker, listener and bridge, boots gem5, and starts the guest
talker, listener and bridge automatically via readfile. The simulation pauses
at the guest workbegin marker, then the host advances it. Guest output appears
on gem5's serial console/output (normally `m5out-talker-listener/system.pc.com_1.device`).
The host listener prints strings from the guest talker; the guest listener prints
strings from the host talker. There is no Gazebo dependency for this test.

For a bounded run or different pacing:

```bash
ros2 launch talker_listener_bridge bringup.launch.py \
  gem5_root:="$(realpath ../../gem5)" steps:=100 \
  interval_us:=100000 poll_us:=10000 ratio:=1.0
```

`steps=0` runs until stopped; `ratio` is simulated seconds per wall second.
Require `0 < poll_us < interval_us <= 3600000000`. Other launch arguments:
`startup_timeout_s` (300), `timing_socket` (`/tmp/chimaera_time.sock`),
`outdir` (`m5out-talker-listener`), and optional absolute `image` and `kernel`
paths. The local gem5 config retains the existing socket-server stepping and
KVM model; its resource paths are passed explicitly rather than inferred from
its installed location.

To run an already deployed simulator separately, launch `host.launch.py`, then:

```bash
../../gem5/build/X86/gem5.opt --outdir=m5out-talker-listener \
  src/talker_listener_bridge/config/gem5_talker_listener.py \
  --gem5-root "$(realpath ../../gem5)"
```

Ctrl+C or completion of `steps` stops the host controller and simulator; any
component exit shuts down the launch. A currently running step may take time to
finish. Only one Chimaera transport session may run at once: data socket paths
are fixed by gem5. Do not run the transport console examples alongside this
application. Existing socket paths are never deleted on startup; after a forced
kill, verify no owner remains before removing stale sockets.

`bridge_test` exercises configuration validation, local ROS String/Int32 routing
in both directions, QoS, empty/UTF-8/max-size strings, malformed frames, queue
limits, and echo isolation. `mock_bridge_integration` uses the sibling `mock-sim`
controllers, separate bridge processes in ROS domains 198/199, and external
`demo_nodes_cpp` talkers/listeners. It checks messages reach both listeners across
controller barriers without starting gem5 or using guest m5 operations.

Tests default to using `../../mock-sim`; override with `-DMOCK_SIM_ROOT=/path/to/mock-sim`,
or disable that test with `-DBUILD_MOCK_BRIDGE_TEST=OFF`. To build and test without
libm5, also pass `-DBUILD_GUEST_BRIDGE=OFF`. Mock tests require Linux pidfd support
and permission for local DDS and Unix sockets. Actual gem5/image boot remains a
separate validation step requiring the deployed offline image and KVM.

## Host status bar

An interactive terminal displays a fixed, highlighted bottom row while logs
scroll above it. This works both with `ros2 run` and the supplied launch files.
It shows the startup wait and timing socket, then connection/activity state,
TX/RX message counts, outgoing queue depth, achieved/target simulation rate,
simulated time, completed steps, time since the last received message, local ROS
publisher/subscriber counts, and average rate (as terminal width permits).

`TIMING READY` means the timing server is available; `ACTIVE` means guest data
has been received. RX age shows how old that evidence is; this is not a separate
heartbeat. TX counts messages handed to the controller, not delivery acknowledgments;
RX counts guest messages published to local ROS. Boot time is excluded from rates.

The bar refreshes every second during startup and pacing, and at interval
boundaries when due. A blocking gem5 step delays refresh until it finishes.
Resize is handled on the next refresh. Shutdown/error cleanup restores the
terminal scrolling region. Redirected output and `TERM=dumb` omit the bar;
normal startup, error and final-summary logs remain available.

Use `status_bar:=false` to disable it, or `report_seconds:=0.5` for a faster
refresh, with either host launch file. For direct execution use ROS parameters,
for example `ros2 run talker_listener_bridge host_bridge --ros-args -p status_bar:=false`.
