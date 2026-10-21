# Chimaera Fast DDS middleware implementation

Work branch: `feat/chimaera-dds-middleware`, based on `ed9c7a6`.

## Objective and acceptance

Route configured ROS topics between their original publishers and subscribers
through a Fast DDS transport backed by Chimaera's existing timed controllers.
Application publish/subscribe APIs stay unchanged. The bridge processes retain
simulation scheduling and device ownership, but do not subscribe to or republish
application data in this mode.

Acceptance requires a complete ball-catching trial, initially using KVM, and a
matched message-latency comparison. The initial robot case is the existing lofted
3 m/s throw, 480x360 stereo at 30 Hz, 250 Hz control, no memory aggressor. Retain
catch scoring, perception/planning/control records and exact artifact identities.
KVM establishes execution correctness; detailed CPU runs establish simulated
latency. Compare source/receipt counts, ordering, frame age, callback latency and
wall time against the current bridge, including native references.

## Design

The transport carries RTPS packets, including discovery and reliability traffic,
through bounded per-side runtimes. A runtime owns the DMA device once and serves
the ROS application processes over local IPC. The existing timing controller
continues to admit host input and expose guest output at completed boundaries.
Transport callbacks never advance either simulator or invoke application callbacks.

A pinned `rmw_fastrtps` 6.2.10 overlay installs the descriptor in participating
processes. Endpoint hooks translate selected host/guest topic names to a common
wire name and advertise Chimaera locators for the configured endpoints. Ordinary
local Fast DDS transport remains available. The first implementation preserves
normal typed deserialization; adopting image buffers in application callbacks is
a later, independently measured optimization.

Integration must account for route direction/type validation, local fanout,
ROS graph visibility, executor wakeups, discovery during startup, reliable
backpressure across paused peers, stereo pairing, and shutdown with held buffers.
Keep unsupported configurations explicit rather than silently changing delivery
semantics. Do not describe the implementation as zero-copy.

## Progress

- Created the feature branch from the current transport implementation.
- Confirmed the disposable Docker SDK contains ROS Humble, Fast DDS 2.6.12,
  rmw_fastrtps 6.2.10 and rclcpp 16.0.21, with KVM passthrough available.
- Implemented the custom Fast DDS transport and pinned RMW hooks, bounded
  per-side routers, middleware selection, relocatable guest staging and ROS
  graph alias queries. An explicit ABI marker rejects unpatched RMW plugins.
- Native transport tests pass: three complete 640x480 images cross the
  coordinator-controlled path with every pixel checked; reverse acknowledgements,
  graph discovery and alias queries work with normal `rclcpp` APIs. Router tests
  cover boundary admission, malformed packets, queue overflow, endpoint reference
  counts and connection cleanup.
- 223 bridge/ball workflow tests pass, with two environment-dependent skips;
  both robot CTest targets pass. The native baseline catches the ball, with
  `inside_cup_at_deadline` and `cup_capture` scoring.
- Rebuilt the simulator with `ed9c7a6`'s interrupt acknowledgement fix after
  identifying lost ATA interrupts in the older SDK binary. The corrected binary
  boots the four-core guest and reaches the application workbegin barrier.
- Removed the UDP packet-size ceiling for explicit local-only participant
  profiles. A full 640x480 RGB image now crosses the tested path in one RTPS
  packet. The matched benchmark gives both implementations the same 1 MiB
  SHM-only profile to isolate middleware handoff costs.
- End-to-end acceptance and matched performance measurements remain in progress;
  no latency improvement has yet been measured.
