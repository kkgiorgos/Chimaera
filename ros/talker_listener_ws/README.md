# Talker–listener across gem5

Two C++ support nodes connect the standard ROS 2 `demo_nodes_cpp` talkers and
listeners through Chimaera's sibling `gem5-transport` project. Both directions
run together:

```text
host talker  -> /host/chatter -> host_bridge -> gem5 -> guest_bridge -> guest listener
guest talker -> /guest/chatter -> guest_bridge -> gem5 -> host_bridge -> host listener
```

The host runs ROS domain 41 and the guest domain 42, both localhost-only.
Separate source and destination topics prevent feedback. Each bridge sends the
exact `std_msgs/String.data` bytes, including empty strings and UTF-8 text. ROS strings are text, not binary payloads.
There are at most 128 pending outgoing messages, each at most 4096 bytes;
exceeding either limit fails the bridge instead of silently dropping its queue.
ROS QoS is reliable, keep-last 128; this is a demo, not an end-to-end delivery
acknowledgment protocol.

The host bridge owns `Gem5HostController`, its transport worker and the timing
controller. It advances gem5 at synchronization boundaries and paces simulated
time against wall time. The guest bridge owns `Gem5GuestController`, links the
custom **libm5.a**, maps m5 memory, and issues the workbegin marker before polling.
ROS callbacks and controller callbacks share one thread in each bridge. Guest
callbacks are pumped during polling, even while `run_next()` has not returned.
The console/channel-service examples are not needed for this application: the
support nodes are the controller producers and consumers themselves.

## Build

From this workspace, on Ubuntu 22.04 with ROS Humble, `demo_nodes_cpp`, colcon,
CMake >= 3.22 and the custom x86 gem5/libm5 build available:

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
its startup script using the existing transport image installer (requires sudo):

```bash
./deploy_guest.sh
# Optional image and partition:
./deploy_guest.sh /absolute/path/to/disk.img 2
```

The image must already contain ROS Humble and `demo_nodes_cpp`, execute the gem5
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

The bridge test exercises real local ROS publication/subscription and transport
payload conversion, empty and UTF-8 strings, maximum payload size, oversize
rejection, and echo isolation. It does not execute guest m5 operations. Full
simulation validation additionally requires the deployed offline image and KVM.

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
