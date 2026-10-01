# Universal ROS bridge simplification audit

The supported runtime is ROS on the host connected through gem5-transport to ROS
inside the modified gem5 guest. There is no supported local guest simulator.
The universal package remains independent of application workspaces. This audit
covers `ros/chimaera_ros_ws`, its talker/listener example, and obsolete top-level
transport helpers; the specialized wall-follow runtime is outside this change.

## Removed from the active project

- `mock-sim` moved to `legacy/mock-sim`, alongside its demos and protocol docs.
- `queue-manager` moved to `legacy/queue-manager`. Neither active ROS bridge links
  it: gem5-transport already owns controller buffering.
- The universal mock adapter, external-node integration test, and test routes
  moved to `legacy/chimaera-ros-mock-tests`. The build no longer imports mock-sim
  or exposes `BUILD_MOCK_BRIDGE_TEST` / `MOCK_SIM_ROOT`.
- The universal package's unused C++ `ament_index_cpp` dependency and its
  mock-only `demo_nodes_cpp` test dependency were removed. The demo application
  still declares its own runtime dependencies. Python package lookup remains
  necessary for the session runner.
- Python test discovery now shares the single `BUILD_TESTING` block. Its explicit
  interpreter lookup is required only for testing.
- gem5's `--boot-to-controller` flag and dormant shell-boot branch were removed.
  The flag was always true; the supported configuration always boots the guest
  startup command and waits at workbegin before opening the timing socket.
- Wall-time ratio control and the host pacing loop were removed. The former
  gem5-transport pacer is archived in `legacy/wall-clock-pacer`; the session
  no longer accepts `ratio`. Status display reports progress without throttling.
- The root guide now starts with the gem5 talker/listener session. Active docs no
  longer advertise mock demos or queue-manager as prerequisites.

## Kept deliberately

The bridge's serialized ROS publishers/subscriptions and explicit route QoS let
unmodified applications communicate without compiling message-specific bridges.
The route identity detects differing topic/type/QoS definitions when data arrives;
remapping validation prevents local topic loops. Frame and queue limits bound
application input. These checks serve the gem5 runtime and are not compatibility
layers for the retired mock simulator.

There are two buffering stages: the ROS node holds messages until its next
exchange, and the transport controller holds messages until its next transfer.
They have distinct ownership and scheduling. Removing the ROS queue requires
redesigning how executor callbacks submit to the controller and how limits are
applied; this audit leaves that contract intact. The generic `DataController`
interface belongs to gem5-transport itself and is also useful for focused tests.

The host-only build option supports separate host and image-compatible guest
build environments. It does not provide a local guest runtime. Guest address-mode
libm5 mapping, workbegin bootstrap, timing readiness, interval/poll scheduling,
and shutdown remain essential to the current KVM gem5 configuration.

The manifest, runner, staging, and offline installer retain separate jobs:
configuration validation, process supervision, application builds, guest path
relocation, and image deployment. Removing them would remove the existing
unmodified-application workflow. `run --side guest` remains the staged entry point
inside gem5. Status display remains optional observability for long simulations.

## Verification and limits

Both universal bridge executables built against the real gem5-transport libraries
and custom libm5; the talker/listener package also built. Both ROS package test
targets passed (bridge routing and five Python manifest/staging/supervision tests).
All three gem5-transport test targets passed: host-controller, controller-protocol
and guest-m5ops. Socket-dependent checks were rerun outside the sandbox to allow
Unix sockets and DDS discovery. The installed CLI validated the example's two
routes, and the edited gem5 configuration passed Python syntax compilation.

These tests do not replace an end-to-end guest boot. The current environment has
no `/dev/kvm`, which the supplied gem5 configuration requires; a real boot cannot
be verified here. The configuration remains KVM-only. It does not switch to a
modeled CPU for hardware statistics. Topic services/actions, automatic clock
management, and a startup route handshake are also outside the current contract.
