# Mock simulator transport and controllers

C++20 implementation of the sequential transport described in
[docs/transport-spec.md](docs/transport-spec.md), using Linux Unix-domain sockets,
and the control loop in [docs/controller-spec.md](docs/controller-spec.md).

## Build and Neovim

Use `make clean` to remove the build directory, including generated binaries and
the compilation database. Run `make` to recreate it.

After an abnormal demo exit, stop both sides and run `make clean-sockets` to
remove stale transport and controller sockets. It skips missing paths and
refuses socket paths still listed in `/proc/net/unix`, ordinary files, and
symlinks. It checks without connecting to the demos. Run it in the same network
namespace, use the exact endpoint paths used to launch the demos, and do not
restart them until cleanup finishes. For custom endpoints:

```sh
make clean-sockets ENDPOINT=/tmp/my-transport CONTROLLER_ENDPOINT=/tmp/my-controller
```

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
After a forced kill, stop both sides and run `make clean-sockets`. Use a private directory for the socket when isolation is
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

## Controllers

`include/mock_sim/controller.hpp` defines the host and guest controller interfaces,
`TimingController`, and the application-owned `DataProducer`/`DataConsumer`
interfaces. `take()` removes one queued byte message (or returns `nullopt`);
`submit()` accepts an owning message. Serialization stays in the application.
Handlers must return promptly and report exceptions using `std::exception` subclasses. They are called serially, with no internal worker
threads; do not re-enter a controller from its handlers.

`include/mock_sim/mock_controller.hpp` provides the concrete implementations.
Construct a `MockHostController` with an endpoint, timing controller, producer,
and consumer; construct a `MockGuestController` with the same endpoint and its
own producer and consumer. Dependencies must outlive their controller.

The guest **must run in a separate process**. After a short bootstrap exchange,
it stops itself with `SIGSTOP`; every interval ends with another process-wide
`SIGSTOP`. No guest thread, protocol handling, application callback, or caller
code executes while paused. The host uses kernel peer credentials and a Linux
pidfd to control the correct process, and checks that **all guest threads are
stopped** before boundary processing. This mock requires Linux 5.3+ with `/proc`
mounted and permission to signal the guest (normally the same user).

The host calls `step(interval, poll_interval)`. The guest repeatedly calls
`run_next()` until it reports `stopped` or `failed`. The call for a completed
interval cannot return while stopped: it returns on the **following resume**,
after which the guest loop calls `run_next()` again for the new interval.
Bootstrap and final teardown are explicit lifecycle phases, not simulation work.

For each interval:

1. While the guest is stopped, the host collects currently queued producer data
   and queues small interval metadata. It starts the timing component, then
   resumes the guest with `SIGCONT`.
2. Only after resume does the host transmit the collected batch. The guest
   receives it and delivers it at its first polling point in this same interval.
   Subsequent polls drain the guest producer, with sleeps between polls.
3. The host drains guest transport frames during the interval into a host-owned
   buffer. The guest emits a completion marker and stops itself. The host
   verifies the actual process stop, then waits for the timing component.
4. With the guest fully stopped, the host delivers incoming guest messages to
   its consumer. It neither sends payloads to the paused guest nor waits for
   guest acknowledgements. Host messages produced after the initial collection
   (including responses from this consumer) are collected on the next step.

The host-side buffering adapts the blocking mock transport to the controller's
asynchronous semantics. Large messages cannot deadlock a boundary by filling a
stopped guest's socket buffer: payload transfer happens only while it is running.
Host callbacks run only while the guest is stopped. Use a
dedicated endpoint, not one shared with the raw transport demo.

`MockTimingController` uses `steady_clock` and sleeping to stand in for two
simulated timelines. Process pause/resume is implemented by the mock controllers;
no robotics simulator is launched. The guest cooperatively reaches each boundary,
so scheduling, callbacks, and IPC may overrun the requested wall-clock duration.
This is not a hard-deadline preemptive simulator, but **once paused no guest code
executes until resumed**. Replace the abstract timing/controller implementations
for real simulator scheduling.

`stop()` explicitly resumes the guest for final protocol shutdown (no producer or
consumer callbacks), without draining the producer. Advance another interval first if queued data
needs to be consumed. Host failure or destruction without successful
`stop()` terminates the attached guest process rather than allowing simulation
work outside an interval or leaving a stopped process behind. The process owner
must reap the guest if it is a child; the guest demo launcher does this
automatically. An abrupt host kill requires external supervision; close the guest
launcher with Ctrl+C to terminate its worker if the host disappears.
Both sides must service the protocol; a stalled running peer can block operations.

Durations must satisfy `0 < poll <= interval <= 1 hour`, with at most 100000
polls per interval. Each guest polling/host step batch is limited to 1024 messages and
64 MiB; the host also bounds the total guest data per interval to those limits.
A producer with more than 1024 queued messages leaves the remainder for its next
poll/step. Oversized batches fail rather than dropping messages silently.
Protocol, transport, and handler exceptions produce a terminal `failed` result
and close the connection; a host failure also terminates its attached guest. Invalid arguments to `step` are
recoverable. Messages already removed from producers are not retried on failure.

Link consumers through `MockSim::controller`; concrete libraries are
`MockSim::mock_host_controller`, `MockSim::mock_guest_controller`, and
`MockSim::mock_timing`. Each controller library links its corresponding transport.

### Interactive controller demo

Run two separate binaries in two terminals. Start the host first:

```sh
make demo-host
```

In the second terminal:

```sh
make demo-guest
```

Or, after `make`, launch `./build/mock_controller_host [ENDPOINT]` and
`./build/mock_controller_guest [ENDPOINT]` directly. Both default to
`/tmp/mock-controller-demo`. For another session, pass the same
`CONTROLLER_ENDPOINT=/tmp/another-controller` to both Make commands (or the same
path argument to both binaries). Start only one pair per endpoint. The host waits
for the guest and performs a short initial interval before displaying its prompt.

Both sides accept `send TEXT`, or `send` alone for an empty message. Received
messages are displayed in the receiving terminal; there is no automatic echo.
Send and receive lines include a `[step N]` timestamp and the message text.
Step 0 is the automatic startup interval; the first manual `step` is step 1.
Both sides count intervals independently, so matching send/receive numbers let
you check synchronization. For example, sending `hello` from the host shows
`[step 1] Sending to guest (5 bytes): hello` on the host and
`[step 1] Received from host (5 bytes): hello` on the guest. Queue confirmation
shows the current completed step and the intended next step; sending is logged
when the producer hands data to the controller (before transport success).

Only the host accepts `step [interval_ms poll_ms]` (defaults: 100 ms and 10 ms)
and `quit`. For example:

1. On the host, enter `send hello guest`.
2. On the guest, type `send hello host` and press Enter.
3. On the host, enter `step`. The guest displays `hello guest` and reads its own
   typed line during that interval; the host displays `hello host` at the boundary.
4. Enter `quit` on the host to shut down both sides.

The guest's controller process is OS-stopped between intervals. Terminal echo
while it is stopped is performed by the terminal driver, not guest code. Typed
lines remain buffered until resume; incomplete lines never block the controller.
For input during a longer running interval, use e.g. `step 5000 100` on the host.
Guest console input is checked at polling points and limited to 1 MiB of buffered
text. Guest EOF stops reading input but continues receiving until host shutdown;
host EOF acts as `quit`.

The guest binary has a small supervisor process that only waits for the actual
guest worker. This keeps the shell from reclaiming its terminal each time the
worker stops. The supervisor does not read input or run any controller callbacks.
Closing that launcher also kills its worker, even if the worker is paused. Normal
host shutdown removes the socket endpoint. After an abnormal exit, stop both sides and use `make clean-sockets`.
