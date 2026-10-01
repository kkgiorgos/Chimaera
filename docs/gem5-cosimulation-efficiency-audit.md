# Chimaera gem5 cosimulation efficiency audit

Audit date: 2026-09-29.

The audit covered gem5's Chimaera m5 handlers, the transport and controllers,
ROS message bridges, Gazebo synchronization, and host recording. It examined
the current working tree, including existing uncommitted changes, based on
commit `94bca6502aaf7593d1723e637b8dcafe3debb624`.

Findings are ordered by likely impact. Performance estimates are derived from
the code and dependency sources; they are not measured speedups. No
implementation changes or new simulation runs were made during the audit.
The saved run artifacts used KVM, whereas the current wall-follow configuration
switches to TimingSimpleCPU by default, so the older timings were not treated
as benchmarks of the current implementation.

1. **High — Gazebo synchronization waits on a throttled telemetry topic.**

   Each interval waits for `/world/.../stats` to confirm completion. Installed
   Gazebo 6.18 publishes this topic at 10 Hz, implying a steady-state ceiling
   around 0.5× speed with the default 50 ms intervals. A dedicated
   step-completion notification would remove this bottleneck.

   Sources: [controller barrier](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/timing_controller.hpp#L131),
   [Gazebo 6.18 source](https://github.com/gazebosim/gz-sim/blob/ignition-gazebo6_6.18.0/src/SimulationRunner.cc#L588).

2. **High — Every interval includes 40 ms of unconditional settling.**

   `settle()` waits 20 ms before physics and another 20 ms afterward, consuming
   80% of the default 50 ms real-time budget before simulation costs. Explicit
   command-consumed and sensor-ready acknowledgments could replace these waits
   while making synchronization more dependable.

   Source: [settling implementation](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/timing_controller.hpp#L171).

3. **High — Gazebo and the host both apply wall-clock pacing.**

   Generated worlds set `real_time_factor=1`; Gazebo applies pacing during
   multistepping, and the host also runs `WallClockPacer`. Because gem5 and
   Gazebo execute serially, Gazebo consumes approximately the interval's wall
   budget itself. Allowing controlled physics steps to run unthrottled would
   leave pacing to the host.

   Sources: [world configuration](../ros/wall_follow_ws/benchmarking/wall_follow_benchmark/world.py#L39),
   [host pacer](../ros/wall_follow_ws/src/wall_follow_bridge/src/host_bridge.cpp#L126),
   [Gazebo pacing source](https://github.com/gazebosim/gz-sim/blob/ignition-gazebo6_6.18.0/src/SimulationRunner.cc#L696).

4. **High — Every poll requires four m5 calls and four fresh socket connections.**

   Both directions transfer length and payload separately. Even an empty
   application exchange contains controller packets, so it pays the full
   cost—nominally about 400 connections per simulated second at 10 ms polling.
   Combined framing and persistent connections could substantially reduce
   overhead. These changes would require coordinated updates to gem5 and the
   transport protocol.

   Sources: [guest framing](../gem5-transport/src/guest_transport.cpp#L18),
   [gem5 socket creation](../gem5/src/sim/pseudo_inst.cc#L663),
   [host packet acceptance](../gem5-transport/src/host_transport.cpp#L152).

5. **High — Blocking socket I/O runs inside gem5's simulation execution path.**

   The m5 handlers synchronously connect, read, and write without deadlines.
   Host scheduling delays therefore stall simulation progress; an unresponsive
   peer can prevent the step from returning. Bounded I/O or staged asynchronous
   transfers could improve throughput and responsiveness, while preserving
   pause semantics.

   Sources: [receive handler](../gem5/src/sim/pseudo_inst.cc#L694),
   [socket I/O helpers](../gem5/src/chimaera/util.cc#L52).

6. **Medium — Fixed polling adds empty exchanges and reaction latency.**

   The guest collects outgoing data before receiving host inputs, then sleeps
   after delivery. Commands resulting from those inputs reach a subsequent
   poll; the host releases received commands at the interval boundary.
   Relative sleeps also make effective polling slower than the configured
   period. Readiness-driven collection and deadline-based polling could reduce
   both costs while preserving the synchronization contract.

   Sources: [guest loop](../gem5-transport/src/gem5_guest_controller.cpp#L40),
   [host delivery](../gem5-transport/src/gem5_host_controller.cpp#L77).

7. **Medium — Every m5 transfer produces normal informational logging.**

   Header and payload operations each log, including empty application polls.
   This adds formatting and output overhead directly to simulation execution.
   Debug-gated or sampled logging would help; merely disabling INFO still
   incurs formatting in the current logger.

   Sources: [transfer logging](../gem5/src/sim/pseudo_inst.cc#L681),
   [logger formatting](../gem5/src/base/logging.hh#L100).

8. **Medium — Payloads undergo repeated copying and allocation.**

   ROS bytes are copied into bridge frames, flattened into controller packets,
   copied through gem5 buffers, split into newly allocated decoded messages,
   and copied into ROS publication buffers. Reusable buffers and messages
   referencing an owned batch could reduce this cost, particularly for sensor
   payloads. Buffer ownership must remain valid across pending transfers and
   simulator pauses.

   Sources: [packet encoding and decoding](../gem5-transport/src/controller_protocol.hpp#L59),
   [ROS publication copy](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/bridge.hpp#L113),
   [gem5 transfer buffer](../gem5/src/sim/pseudo_inst.cc#L654).

9. **Medium — Every ROS message repeats its complete route configuration.**

   The route identity includes topics, type, direction, and QoS. Current bridge
   framing adds 105 bytes per clock message and 119 bytes per scan or command,
   excluding controller framing. Negotiated compact route IDs would reduce
   bandwidth, copying, and lookup work while retaining configuration validation
   during setup.

   Sources: [route identity](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/config.hpp#L133),
   [frame construction](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/bridge.hpp#L55).

10. **Medium — Executor polling repeats for every dequeued message.**

    `Bridge::take()` calls `spin_some()`, and batch collection repeatedly calls
    `take()`. Draining N messages generally pumps the executor N+1 times.
    Pumping once before draining a batch could reduce executor work, especially
    inside the simulated guest.

    Sources: [producer callback](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/bridge.hpp#L76),
    [collection loop](../gem5-transport/src/controller_protocol.hpp#L42).

11. **Medium — Timing commands use connection-per-step and byte-by-byte reads.**

    Python receives commands one byte at a time; C++ performs a `poll()` and
    single-byte `recv()` for every response character. Buffered reads and a
    persistent timing connection would remove dozens of syscalls per
    synchronization interval.

    Sources: [C++ response reader](../gem5-transport/src/gem5_timing_controller.cpp#L89),
    [Python command reader](../ros/wall_follow_ws/src/wall_follow_bridge/config/gem5_wall_follow.py#L253).

12. **Medium — Bridge queues retain stale state updates in a shared FIFO.**

    Once subscribed messages enter `outgoing_`, they no longer follow
    per-topic `keep_last` behavior. Clock and sensor updates share the global
    128-message limit, so accumulated stale updates consume processing capacity
    and can terminate the bridge on overflow. Per-route limits and selective
    coalescing could help where intermediate samples are unnecessary. Clock
    coalescing must preserve the intended guest timer behavior.

    Source: [outgoing queue](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/bridge.hpp#L50).

13. **Medium — Recording flushes files on every observation and interval.**

    Pose and command callbacks flush CSV files immediately; the host also
    flushes timing output after every step. Buffered or periodic flushing would
    reduce write calls and collector scheduling delays. The collector also
    acts as the command gateway, so callback overhead can affect subsequent
    command forwarding.

    Sources: [recorder](../ros/wall_follow_ws/src/wall_follow_benchmark/wall_follow_host/recording.py#L58),
    [timing output](../ros/wall_follow_ws/src/wall_follow_bridge/src/host_bridge.cpp#L147),
    [command gateway](../ros/wall_follow_ws/src/wall_follow_benchmark/wall_follow_host/collector.py#L79).

14. **Medium, archived channel-service mode — One full channel can stall the whole cosimulation.**

    Deserialization waits for queue space while holding the bundle's
    deserialization lock. Because submission runs synchronously at the host
    boundary, an undrained channel blocks later channels and the next simulation
    interval. Per-channel admission or credits could isolate backpressure while
    preserving the configured delivery guarantees. This finding applies to the archived
    `ChannelService`; the ROS bridges use their own queues.

    Sources: [blocking insertion](../legacy/queue-manager/src/queue_manager.cpp#L171),
    [channel submission](../legacy/gem5-transport-demos/src/channel_service.cpp#L118),
    [host callback delivery](../gem5-transport/src/gem5_host_controller.cpp#L85).

15. **Lower, archived channel clients — Local IPC repeats the connection and polling overhead.**

    Every send or receive request creates a new socket, while idle clients poll
    every 20 ms. Persistent connections with data-ready notifications would
    reduce empty requests and receive latency.

    Sources: [client requests](../legacy/gem5-transport-demos/src/channel_service.cpp#L122),
    [client polling](../legacy/gem5-transport-demos/examples/channel_console.hpp#L34).

16. **Lower, archived standalone examples — The transport build defaults to unoptimized Debug.**

    The standalone build currently uses `-g` without optimization. Release
    builds would reduce bridge execution costs and simulated guest instruction
    overhead. This finding applies to the standalone transport examples; the
    inspected wall-follow build uses Release.

    Sources: [build default](../legacy/gem5-transport-demos/Makefile#L2),
    `legacy/gem5-transport-demos/build/CMakeFiles/gem5_guest_controller.dir/flags.make`,
    `ros/wall_follow_ws/build/wall_follow_bridge/CMakeCache.txt`.
