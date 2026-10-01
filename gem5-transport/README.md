# gem5 transport

This package exchanges messages between a host application and an x86 Linux
application running inside the modified gem5 simulator. It provides synchronous
raw transports and buffered controllers for applications that advance simulation
in intervals. Applications supply their own producers, consumers, and run loops.

Read [CONTROLLERS.md](CONTROLLERS.md) for controller integration, the execution
sequence, timing, and protocol design. The ROS integrations are in
[wall-follow](../ros/wall_follow_ws/README.md) and
[talker/listener](../ros/talker_listener_ws/README.md).
Interactive demos, local channel IPC, and image deployment helpers live in the
[self-contained legacy archive](../legacy/gem5-transport-demos/README.md).

## Build and link

Requires Linux, CMake 3.21+, Make, and a C++20 compiler:

```sh
make -C gem5-transport
# Build just the guest libraries:
make -C gem5-transport guest
# Host libraries and tests without libm5:
make -C gem5-transport BUILD_DIR=build-host CMAKE_ARGS=-DGEM5_TRANSPORT_BUILD_GUEST=OFF
```

`BUILD_DIR`, `BUILD_TYPE` (default Debug), `JOBS`, `GEM5_ROOT`, and `CMAKE_ARGS`
are configurable. Use `BUILD_TYPE=Release` for simulation workloads.
The guest libraries require the custom x86 `libm5.a` from
`gem5/util/m5/build/x86/out/`; build it with `scons build/x86/out/m5` from
`gem5/util/m5`. Point `GEM5_ROOT` at another compatible tree or set the CMake
cache variable `GEM5_M5_LIBRARY` to its library. Guest binaries must be compatible
with the simulated OS's C++ runtime.

```cmake
add_subdirectory(path/to/gem5-transport)
target_link_libraries(your_host PRIVATE chimaera::host_controller)
target_link_libraries(your_guest PRIVATE chimaera::guest_controller)
```

| Target | Interface | Purpose |
| --- | --- | --- |
| `chimaera::transport` | `chimaera/transport.hpp` | Raw message contracts and result types |
| `chimaera::host` | `chimaera/host_transport.hpp` | Synchronous host socket transport |
| `chimaera::guest` | `chimaera/guest_transport.hpp` | Synchronous guest m5op transport |
| `chimaera::host_controller` | `chimaera/gem5_controller.hpp` | Buffered host exchange and timing; wall-clock pacing |
| `chimaera::guest_controller` | `chimaera/gem5_controller.hpp` | Buffered guest polling |

Targets propagate interface headers and C++20. The guest target links libm5 and
propagates `-no-pie` for its x86 assembly. Applications using `m5_mmap.h` or
`gem5/m5ops.h` directly must add `${GEM5_MMAP_INCLUDE}` or `${GEM5_OP_INCLUDE}`
to their own include paths.

```sh
ctest --test-dir gem5-transport/build --output-on-failure
```

The tests exercise host exchanges against a socket peer, controller queues and
encoding, and guest operations against fake m5ops. They do not boot gem5.

## Raw transport contract

Include the host or guest header and use the concrete object through
`chimaera::Transport&`. `send(span)` transfers one complete message;
`receive()` blocks for one message and returns owned bytes. Check `result.ok()`
and inspect `result.error` / `result.message` on failure. An empty message is
valid and differs from a failed receive. Operations on an instance must be
serialized; use one transport session per simulator.

Construct `HostTransport` before any guest transfer. It immediately opens
`/tmp/chimaera_g2h.sock` and `/tmp/chimaera_h2g.sock`, matching
[gem5's constants](../gem5/src/chimaera/util.hh). Alternate paths require matching
simulator changes. Host sends wait for guest receives, and host receives wait for
guest sends. The simulator must be running to execute guest calls. Two receives
wait indefinitely; the raw transport supplies no timeout or liveness protocol.

The application owns libm5's process-global address mapping. Set
`m5op_addr = 0xFFFF0000`, call `map_m5_mem()` once before address-mode transfers,
and `unmap_m5_mem()` after all users finish. The transport never unmaps it.
Mapping uses `/dev/gem5_bridge` or `/dev/mem`; the latter normally requires root.
An unmapped address-mode operation returns `invalid_argument`.

`GuestM5Ops::address` is the default and supports KVM. Choose
`GuestM5Ops::instruction` for a simulated x86 CPU such as TimingSimpleCPU or O3;
it requires no mapping. Instruction ops must never execute on the host or under
KVM. For a KVM-to-simulated-CPU transition, see the controller bootstrap contract.

## Framing and its reason

A message is an eight-byte unsigned big-endian payload length followed by the
payload, capped at 64 MiB. Header and nonempty payload use separate m5 calls.
The guest first learns the length, then requests exactly that many bytes.
Empty messages use just the header: zero-length m5 calls are avoided because
the simulator handlers index the first and last buffer bytes.

Each m5 call opens a fresh Unix connection. Guest-to-host operations add a
native host `uint64_t` envelope around each call's bytes; the host validates and
strips it. Host-to-guest operations send exactly the bytes the guest requested,
without that envelope. The native envelope comes from gem5 on the same host;
the message length remains big-endian. This adapts the existing pseudo-op ABI.
Transfers add no modeled communication latency.

## Failures and lifetime

Oversized local sends and missing guest mappings are rejected before transfer
without poisoning the raw transport. Socket failures, malformed incoming frames,
allocation failures, and short m5 returns store a permanent failure; further
operations return it. Restart both ends together after a partial transfer.
A successful send acknowledges transport completion, not application handling.

`HostTransport::cancel()` is the only operation allowed from another thread.
It permanently wakes blocked I/O. Join the I/O thread before destroying the
transport. Owned sockets are removed on normal destruction only if their file
identity still matches. Existing paths are never unlinked at startup. After a
forced kill, use `ss -xapn` to check ownership before removing stale paths.
Startup failures are stored too, so recreate a failed instance.

The simulator's m5 handlers panic on socket failures; the adapter cannot turn
those panics into guest result errors. The libm5 mapping helper can also exit
on setup failure. Controller shutdown and failure behavior are documented
separately in [CONTROLLERS.md](CONTROLLERS.md).
