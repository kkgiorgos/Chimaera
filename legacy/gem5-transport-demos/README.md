# Archived gem5 transport demos

This is a standalone snapshot of the raw transport and buffered controller demos
from repository revision `a4ffea2a54d184db4ae37d9c3b0a3c087c4d7c56`, archived
on 2026-10-01. It preserves their build dependencies so future changes to the
active transport API cannot break these demos.

## Build

From the repository root:

```sh
make -C legacy/gem5-transport-demos
ctest --test-dir legacy/gem5-transport-demos/build --output-on-failure
# Optional host-only build:
make -C legacy/gem5-transport-demos BUILD_DIR=build-host CMAKE_ARGS=-DGEM5_TRANSPORT_BUILD_GUEST=OFF
```

Requires Linux, CMake 3.21+, Make, a C++20 compiler, and C/assembly tooling for
x86 guest builds. Python 3 enables the channel-console test. Build in a separate
CMake project: the archived and active libraries use the same target names.

The archive includes its own `include/`, `src/`, examples, and tests;
`vendor/queue-manager` is a complete source snapshot of its queue dependency.
`vendor/m5` contains the x86 instruction/address assembly, mapping helper, and
headers needed to compile the guest programs. CMake builds those sources directly;
it does not use the active transport, active queue-manager, or an external libm5.
Original gem5 copyright notices and license files are retained.

The copied simulator configuration is in `configs/x86-ubuntu-socket-server.py`.
Running a guest still requires a compatible modified gem5 binary, kernel, and
Linux disk image. These runtime resources are not bundled. `GEM5_ROOT` defaults
to `../../gem5` for deployment image selection; the copied config locates runtime
resources under the repository's `gem5/resources`. The source revision above
also identifies the compatible simulator implementation; a future simulator ABI
change may require checking out that revision separately.

See [RAW_TRANSPORT.md](RAW_TRANSPORT.md) for raw send/receive demos and image
deployment, and [CONTROLLERS.md](CONTROLLERS.md) for controller/channel demos.
Deployment remains opt-in and requires an offline image. The guest executable's
C++ runtime must match the guest OS.

## Known issues retained for future work

These issues were deliberately archived without fixes. Existing passing tests
do not cover these failure cases.

1. **Full incoming queues can disable graceful host shutdown.** In
   [examples/controller_host.cpp](examples/controller_host.cpp), the application
   thread calls `service.exchange()` synchronously. A full incoming channel
   blocks deserialization until a client drains it. That thread also handles
   quit and checks the interrupt flag; SIGINT/SIGTERM only set the flag and
   cannot wake deserialization. With no draining client, timing and shutdown
   can remain blocked indefinitely. A future revision needs cancellable delivery
   and a shutdown path able to call `ChannelService::close()` while delivery waits.
2. **Oversized incoming channel messages can be lost.**
   [ChannelService::submit](src/channel_service.cpp) accepts serialized payloads
   above the advertised 4096-byte limit. The worker dequeues one and sends it,
   but the client rejects the oversized reply after the dequeue. A 4097-byte
   payload reproduced the error and an empty queue afterward. Future work should
   validate incoming record sizes before publishing them.
3. **Destruction can unlink a replacement service's socket.**
   [ChannelService::Impl](src/channel_service.cpp) unconditionally unlinks its
   path. If the path is removed while the first instance lives and a second
   instance binds it, destroying the first removes the second instance's path.
   This was reproduced locally. Future work should use the device/inode ownership
   check already present in [HostTransport](src/host_transport.cpp).

The archive only changes build paths/dependency wiring, documentation, the copied
config's resource location, and the guest demo's unreachable stopped-return branch.
The reviewed channel behavior is preserved.
