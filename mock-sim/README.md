# Mock simulator transport

C++20 implementation of the sequential transport described in
[docs/transport-spec.md](docs/transport-spec.md), using Linux Unix-domain sockets.

## Build and Neovim

Use `make clean` to remove the build directory, including generated binaries and
the compilation database. Run `make` to recreate it.

Use `make` to build, then run the host and guest in separate terminals as shown
below.

Requires Linux, CMake 3.21+, Make (or Ninja), and a C++20 compiler.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

Configuration generates `build/compile_commands.json`. The checked-in `.clangd`
points clangd there, so your Neovim clangd client can find include paths and the
C++20 language setting. Install clangd and enable it in your Neovim LSP setup;
restart the client if you opened files before configuring. No machine-specific
include paths are required. Reconfigure after adding source files or changing
compiler flags. If you change the build directory, update `.clangd` accordingly.

## Layout

- `include/mock_sim/transport.hpp`: abstract `Transport` interface and result types.
- `include/mock_sim/mock_host_transport.hpp` and `src/mock_host_transport.cpp`:
  host socket implementation.
- `include/mock_sim/mock_guest_transport.hpp` and `src/mock_guest_transport.cpp`:
  guest socket implementation.
- `examples/host.cpp` and `examples/guest.cpp`: separate executables, each linked
  only to its own mock library; their transfer logic takes a `Transport&`.

The scaffold treats each send as one complete message and each receive as a
blocking operation returning an owning byte vector. Empty messages are valid;
an error is distinct from an empty payload. These are explicit scaffold choices
where the original spec leaves details open. Calls must be serialized, and the
sender's buffer is needed only for the duration of `send`. Errors carry a code
and a diagnostic string; `ok()` checks the code.

Both constructors accept the same filesystem socket path. Initialization happens
on the first send or receive. The host binds and listens, then waits for one guest;
the guest retries connection for up to five seconds, so either process can start
first. Once connected, operations block until complete or a socket error occurs.
The host's accept has no timeout. Callers must arrange complementary send/receive
operations; simultaneous large sends can block.

Messages use an eight-byte unsigned big-endian length followed by the payload.
The maximum payload is 64 MiB. Partial reads/writes and interrupted syscalls are
handled internally. Sending to a closed peer does not raise SIGPIPE. EOF and
connection-reset errors return `disconnected`; other system errors return
`io_error`. Invalid paths and oversized messages return `invalid_argument`.
An oversized local send leaves the connection usable. Connection failures,
malformed incoming frames, and interrupted transfers make that transport instance
unusable: subsequent operations return the stored error. Create a new instance
to reconnect. A successful send means the bytes were accepted by the local socket,
not that the peer application has processed them.

The host removes its socket path on normal destruction. It never removes an
existing endpoint before binding, and the guest never removes the endpoint.
After a forced kill, remove a stale socket yourself after checking that its owner
is no longer running. Use a private directory for the socket when isolation is
needed. `src/socket_transport.hpp` holds the common private framing and socket
logic; no socket implementation details are exposed in the public headers.

## Run the host and guest

In separate terminals, use the same endpoint:

```sh
./build/mock_sim_host /tmp/mock-sim-demo
./build/mock_sim_guest /tmp/mock-sim-demo
```

Each program prompts for a mode: `s` / `send`, `r` / `receive`, or `q` / `quit`.
Choose send on one side and receive on the other. The sender prompts for a line
of text (spaces and empty lines are supported); the receiver prints the message.
After each transfer, both return to the menu, so you can reverse direction.
For example, select `r` on the host, then `s` on the guest and type `Hello host!`.

Receive blocks until a message arrives. On the first transfer the host also
waits for the guest to connect; select the guest's mode within five seconds if
the guest starts the transfer first. Choosing receive on both sides waits
indefinitely; use Ctrl+C to interrupt. `q` or end-of-input exits at a prompt.
Transport errors are printed and end the program with a nonzero exit status.

## Link from a larger project

Use this repository as a subdirectory:

```cmake
add_subdirectory(path/to/mock-sim)
target_link_libraries(your_host PRIVATE MockSim::mock_host)
target_link_libraries(your_guest PRIVATE MockSim::mock_guest)
```

Targets propagate include paths and the C++20 requirement. Libraries are static
by default; use `-DBUILD_SHARED_LIBS=ON` for shared libraries. Set
`-DMOCK_SIM_BUILD_EXAMPLES=OFF` for a standalone library-only build. Examples
default to off when included as a subdirectory.

To add another backend, derive from `mock_sim::Transport`, put the implementation
in its own library target, and link that target publicly to `MockSim::transport`.
Higher-level code can depend on that interface-only target and accept
`Transport&`; the application chooses the concrete implementation.
