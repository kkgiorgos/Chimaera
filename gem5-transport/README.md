# gem5 transport

`chimaera::HostTransport` and `chimaera::GuestTransport` implement the local
[`chimaera::Transport`](include/chimaera/transport.hpp) interface. This directory
contains its own interface, result types, libraries, and interactive examples.
It builds independently of other transport implementations. The host library
uses Unix sockets; the guest library calls the existing
`m5_chimaera_send_addr` and `m5_chimaera_recv_addr` operations from the custom
`libm5`.

For buffered exchange at synchronization intervals and socket-driven gem5
stepping and separate application processes attached to message channels, see
[the controller guide](CONTROLLERS.md). Controller builds also embed the sibling
`queue-manager` library; the raw transport API remains unchanged.

## Build

From the repository root:

```sh
make -C gem5-transport
```

Inside `gem5-transport`, use `make` to build everything, `make guest` to build
only the guest executable, or `make clean` to remove the build directory.
Override settings as needed, for example `make JOBS=8 BUILD_TYPE=Release`.
`BUILD_DIR`, `GEM5_ROOT`, and additional `CMAKE_ARGS` are also configurable.

The guest build requires the custom x86 `libm5.a` at
`gem5/util/m5/build/x86/out/`. If needed, build it from `gem5/util/m5` using
`scons build/x86/out/m5`. Set `GEM5_ROOT` to use another custom gem5 tree, or
`GEM5_M5_LIBRARY` to point to its library. The guest executable must also be
compatible with the guest OS's C++ runtime.

For a host-only build without gem5 headers or libm5, configure with
`make CMAKE_ARGS=-DGEM5_TRANSPORT_BUILD_GUEST=OFF`. To enable the guest again
in that build directory, pass `CMAKE_ARGS=-DGEM5_TRANSPORT_BUILD_GUEST=ON`.

## Deploy to the guest image

Shut down any gem5 or QEMU process using the image, then run:

```sh
make -C gem5-transport deploy
```

This builds the guest executable, requests sudo for mounting, and installs it
as `/usr/local/bin/gem5_transport_guest` in partition 2 of
`gem5/resources/x86-ubuntu-22.04-ros-humble.img`. The image must be an offline
raw disk image. The script creates a temporary mount point and unmounts and
detaches its loop device on exit, including on copy failure.

To use a different image or destination:

```sh
make -C gem5-transport deploy IMAGE=/path/to/disk.img PARTITION=2 GUEST_PATH=/home/gem5/gem5_transport_guest
```

The helper is `scripts/edit_disk.sh`; it can also be invoked directly with
`bash scripts/edit_disk.sh IMAGE EXECUTABLE PARTITION /GUEST/DESTINATION`.

## Interactive examples

Start this on the host **before any guest transfers**:

```sh
./gem5-transport/build/gem5_transport_host
```

After deploying, run this inside the simulated x86 Linux guest:

```sh
sudo /usr/local/bin/gem5_transport_guest
```

The guest example maps the m5 address region at `0xFFFF0000`, using libm5's
`map_m5_mem()`, and unmaps it on exit. Access through `/dev/mem` normally needs
root; the gem5 bridge device can provide access without it. This executable
must run inside the modified gem5 simulation, not directly on the host.

Choose `send` on one side and `receive` on the other. Type a line on the
sending side. Each operation returns to the menu; `quit` exits. Empty lines,
spaces, and direction changes are supported. Both receives and host sends
block waiting for the corresponding guest operation. Two receives will wait
indefinitely. The simulator must be advancing for guest operations to run.

The host opens both `/tmp/chimaera_g2h.sock` and `/tmp/chimaera_h2g.sock` when
constructed, matching `gem5/src/chimaera/util.hh`. Do not run the legacy host
bridges on these paths at the same time. Existing socket paths are never
unlinked during startup; owned paths are removed on normal destruction.
After a forced kill, remove stale sockets only after their owner has stopped.
Use `ss -xapn | grep chimaera` to check for active sockets. If neither path is
in use, remove `/tmp/chimaera_g2h.sock` and `/tmp/chimaera_h2g.sock` and restart
the host. Quit through the menu with `q` for normal cleanup; Ctrl+C can leave
the socket files behind. Restarting is required because startup failures are
stored by the transport instance.
Alternate constructor paths require matching changes to the socket constants
in gem5.

## Linking and mapping ownership

```cmake
add_subdirectory(path/to/gem5-transport)
target_link_libraries(your_host PRIVATE chimaera::host)
target_link_libraries(your_guest PRIVATE chimaera::guest)
```

Include `chimaera/host_transport.hpp` or
`chimaera/guest_transport.hpp` and pass the concrete object as a
`chimaera::Transport&`. Libraries propagate the C++20 requirement and interface
headers. The guest target links libm5 and propagates `-no-pie` for its x86 code.
Applications that manage the address mapping must add
`${GEM5_ROOT}/util/m5/src` to their include paths for `m5_mmap.h`.

The application owns the process-global libm5 mapping: call `map_m5_mem()`
once before using the guest transport and `unmap_m5_mem()` after all operations
finish. The transport does not unmap another component's mapping. An unmapped
guest operation returns `invalid_argument`. Use one sequential transport
session per simulator; concurrent transfers or multiple clients are unsupported.

## Wire protocol and errors

Each message uses an eight-byte unsigned big-endian payload length, followed
by its payload. The length and nonempty payload are **separate m5 calls**.
Thus empty messages need one call, and other messages need two. Zero-length
m5 calls are avoided because the existing handlers index the first and last
bytes of their buffers. The maximum message size is 64 MiB.

Each m5 call opens a separate Unix connection:

- Guest to host: gem5 adds its existing native `uint64_t` packet-length
  envelope before the call's bytes. The host validates and strips this
  envelope before interpreting the message header or payload.
- Host to guest: the host sends exactly the bytes requested by that call;
  no extra envelope is added. Receiving the header first tells the guest
  how many payload bytes to request next.

No changes to the existing gem5 operations or their ABI are required. Both
ends must use this new backend's framing; the legacy random-byte examples
use different connection/framing conventions.

Host socket failures, malformed frames, allocation failures, and short m5
returns produce result errors. After a transfer failure the instance stores
the error and rejects further operations, avoiding reuse of a partial message.
An oversized local send is rejected without poisoning the session. Recreate
both ends together after a failed transfer.

The existing simulator handlers panic on socket failures instead of returning
an error code. Those failures still terminate gem5 and cannot be converted to
a guest `TransportError` by this adapter. Likewise libm5's mapping helper may
exit on setup failure. There is no timeout or peer-liveness channel while
waiting for a new connection, and a successful send is not an application-level
acknowledgment. Transfers do not add modeled communication latency.
