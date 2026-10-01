# gem5 controllers

Controllers buffer application messages while gem5 advances in synchronization
intervals. They do not own application callbacks or a Gazebo connection.
The [wall-follow integration](../ros/wall_follow_ws/src/wall_follow_bridge/include/wall_follow_bridge/timing_controller.hpp)
coordinates gem5 with Gazebo outside this package.

## Application integration

Include `chimaera/gem5_controller.hpp` and link the appropriate controller target
from [the build guide](README.md). The `DataController`, `HostController`,
`GuestController`, and `TimingController` interfaces define the extension contracts.
Applications serialize controller calls and own all message producers and consumers.

A host loop constructs the listeners before starting the guest, waits for the
paused timing server, and exchanges application data around each step:

```cpp
chimaera::Gem5TimingController timing("/tmp/chimaera_time.sock");
chimaera::Gem5HostController host(timing, std::chrono::milliseconds(10)); // fixed polling interval
// timing must outlive host
try {
    timing.wait_until_ready();
    for (int i = 0; i < 10; ++i) {
        host.submit(chimaera::Message{}); // example application message
        const auto result = host.step(std::chrono::milliseconds(100));
        if (!result.ok()) throw std::runtime_error(result.message);
        while (auto message = host.take()) {
            // Deliver *message to your application.
        }
    }
} catch (...) {
    (void)host.stop(); // best effort; a failed timing session may need external termination
    throw;
}
const auto stopped = host.stop();
if (stopped.state == chimaera::ControllerState::failed)
    throw std::runtime_error(stopped.message);
```

Include `<stdexcept>` for this example. The simulator configuration owns the
timing server; [the wall-follow configuration](../ros/wall_follow_ws/src/wall_follow_bridge/config/gem5_wall_follow.py)
is an existing implementation. With workbegin startup it boots first and opens
the timing socket after pausing at the application's marker. The host data
listeners must already exist because bootstrap can execute guest m5 calls.

An address-mode guest initializes the mapping and marker before polling:

```cpp
m5op_addr = 0xFFFF0000;
map_m5_mem();
try {
    chimaera::Gem5GuestController guest;
    m5_work_begin_addr(0, 0); // use with a config that pauses at workbegin
    while (true) {
        // Queue application output with guest.submit(message).
        const auto result = guest.run_next();
        if (!result.ok()) throw std::runtime_error(result.message);
        while (auto message = guest.take()) {
            // Deliver *message and service application callbacks.
        }
    }
} catch (...) {
    unmap_m5_mem();
    throw;
}
```

Include `m5_mmap.h`, `gem5/m5ops.h`, and `<stdexcept>` in addition to the
controller header. Successful host shutdown terminates gem5, so the guest loop
does not receive a stopped result or execute cleanup on that path.

For KVM boot followed by a workbegin switch to a simulated CPU, construct
`Gem5GuestController(GuestM5Ops::instruction, true)`. It uses address ops during
startup and instruction ops after the host supplies the session configuration. Keep the
mapping alive throughout polling. Host readiness is meaningful only when the
simulator configuration guarantees that the CPU switch precedes host stepping.
Pure simulated-CPU operation can use instruction mode without address bootstrap.

`submit(message, key)` queues outgoing data. An empty key preserves FIFO,
including duplicates. A nonempty key replaces pending updates with that key,
putting the newest update after other retained messages. The key is copied,
remains local metadata, and cannot replace data already in flight. Overflow
throws without changing the queue. `take()` returns `nullopt` when empty;
an engaged optional can contain an empty message.

## Why there is an I/O worker

Guest m5 calls synchronously wait for host socket I/O. Meanwhile, the host's
application thread must wait for the simulator to finish its interval. Servicing
both waits on that thread would deadlock. The host controller therefore owns an
I/O worker that handles guest polls independently of the timing wait.

```mermaid
sequenceDiagram
    participant App as Host application
    participant Time as gem5 timing server
    participant Guest as Guest application
    participant IO as Host I/O worker
    App->>Time: STEP_TICKS budget
    Note over Time: Resume guest execution
    Guest->>IO: Startup request (first call only, retry until ready)
    IO-->>Guest: Fixed polling duration
    Guest->>IO: Outgoing batch
    IO-->>Guest: Host batch
    Note over Guest: Deliver messages; next call waits on guest OS clock
    Note over Time: Pause when interval ends
    Time-->>App: OK start_tick end_tick
    Note over App: Snapshot decoded guest batches; take() delivers them
```

Configure the polling interval once in the host controller constructor (default
1 ms); it must be positive and at most one simulated hour. Startup sends this
immutable value to the guest. A guest `run_next()` performs one batch exchange.
Before subsequent exchanges it sleeps for that duration on the guest OS clock.
Returning before that sleep lets the application deliver received messages and
run callbacks promptly. The first call completes startup, then sends queued
application output and receives a batch immediately, without a polling wait.
Actual spacing also includes guest scheduling and application work; the duration is not a guarantee of a fixed poll count.

An empty batch is valid, so polling never waits for application data. Before the
first host step, the guest exchanges startup requests and receives not-ready
replies. It retries after 1 ms on the guest OS clock and retains queued application
messages locally. When the first step starts, a startup reply supplies the fixed
polling interval and completes initialization. The host never puts application
messages in startup replies.

This protects the window in which KVM executes beyond workbegin before gem5
services its global exit. Startup remains inside the first `run_next()` call;
address bootstrap remains active until initialization finishes. Sending a
not-ready reply promptly matters: blocking the bootstrap exchange until host
stepping could prevent the simulator from reaching its startup pause.

## What an interval boundary guarantees

After gem5 confirms its pause, `step()` exposes the current snapshot of fully
decoded guest batches through `take()`. A transfer or worker decoding can cross
the pause. Such data becomes available at a later boundary; no partial message
is delivered. The controller never resumes gem5 just to finish a transfer and
never joins its I/O worker at a boundary.

Polling settings never change during a session. Poll more frequently than the
synchronization interval to reduce exchange latency. An interval is a simulation
pause and receive snapshot, not an acknowledgment that every queued message
reached its application. No interval counter is carried by application batches.

Each controller queue and packet batch is capped at 1024 messages and 32 MiB of
payload. Unconsumed received data counts toward the queue limits. The worker can
also hold a detached batch in flight; these limits are not one global memory cap.
Submitted data leaves the queue when a packet is prepared. Failure afterward
does not requeue it automatically.

The host protects pending incoming and outgoing queues with one mutex. The
worker appends decoded batches using the same receive queue and limits as the
guest. At each completed step, the application detaches the pending incoming
messages under the mutex and appends them to its ready queue outside it. The
worker drains the outgoing queue under the mutex, then assembles and sends the
resulting batch outside it. There is no second outgoing queue for the worker.
Receive storage grows on demand; the host keeps no preallocated snapshot buffer.
These operations move payload buffers without copying their bytes. The receive
cursor avoids shifting every remaining descriptor on each `take()` and reclaims
consumed slots on append.
Public controller calls remain serialized by the application; the mutex only
coordinates the application with the internal I/O worker. Host queue allocation
can add wall-clock overhead but does not add modeled guest instructions.

## Timing and the two clocks

`Gem5TimingController::start(interval)` sends a command and returns;
`wait()` reads completion once the simulator pauses. Only one timing request may
be outstanding, and only this timing session may advance that simulator.
Intervals must be positive and at most one simulated hour. Host `step()` also
requires the configured poll duration to be no greater than the interval and
at most 100000 nominal polls per interval. `step(interval)` does not configure
polling.

The simulator uses a fixed 1 THz tick frequency: one nanosecond equals 1000 ticks.
The controller anchors an ideal cumulative target at the first step's start tick.
Each subsequent interval adds to that target, then requests
`max(0, target_tick - last_actual_tick)`. If a nominal 100 ms step advances 103 ms,
the next 100 ms interval requests 97 ms. Large overshoots can require zero-tick
steps while the ideal target catches up. Requests are capped at one simulated
hour; remaining undershoot carries forward. Integer tick accounting preserves
sub-nanosecond drift. This corrects cumulative progress, not the precision of
individual pause boundaries.

Host intervals advance as soon as the previous exchange completes. No wall-time
ratio or pacing delay is imposed. Simulation tick accounting remains independent
of host execution speed.

## Protocols

The timing protocol is ASCII, with one newline-terminated command per connection:

| Command | Response |
| --- | --- |
| `STEP_TICKS n` | `OK actual_start_tick actual_end_tick`; zero keeps gem5 paused |
| `STEP_NS n` / `STEP_US n` | Same response, after scaling the positive duration to ticks |
| `STATUS` | `PAUSED tick` or `DONE tick` |
| `QUIT` | `BYE`, then simulator shutdown without resuming the guest |

Invalid commands return `ERROR text`; terminating simulation events return
`DONE tick`. Intermediate startup exits do not reset the step budget. The server
runs simulation on its main thread, so neither STATUS nor QUIT interrupts a step.
Disconnecting does not undo a command. The C++ constructor's wall timeout defaults
to 300 seconds. `wait_until_ready()` has a separate startup timeout and an optional
cancellation callback while retrying an unavailable endpoint.

The message protocol has two phases, both inside the [raw transport framing](README.md).
[src/controller_protocol.hpp](src/controller_protocol.hpp) defines their encoding.
All integers below are unsigned, eight-byte, and big-endian.

**Startup:** the guest sends the protocol identifier `CHIMCTR2`. The host replies
with that identifier followed by the polling duration in nanoseconds. Zero means
not ready, so the guest retries after its 1 ms bootstrap wait. A positive value
completes startup. Both ends check the identifier; the guest also rejects
truncated/trailing startup data and durations above one hour. There are no
application messages in startup, and queued data stays intact until it finishes.

**Running:** both directions use exactly the same format:

```text
message_count
for each message:
    message_length
    message_bytes
```

A zero count is an empty batch; a zero message length is an empty application
message. There is no type, epoch, version identifier, or polling duration in a
regular batch. The startup version and ordered request/reply flow identify the
protocol and phase. Batch decoding rejects excessive counts/payload, truncation,
and trailing data. The outer transport already provides total packet length.

The host worker completes its startup reply before accepting batches. The guest
completes startup before sending its first batch, including switching m5op backend
when requested. Pauses can occur during either phase; progress resumes naturally
when the simulator runs again. The protocol has no reconnect/resynchronization
path after a partial transfer. Both peers must use this version; rebuild and
redeploy the guest alongside the host when updating from the previous protocol.

## Shutdown and failures

Call `host.stop()` to send QUIT while paused, then cancel and join local I/O.
The timing controller must outlive the host controller. Destruction alone
cancels local I/O; it does not stop or advance gem5.

| Outcome | What the caller does |
| --- | --- |
| `step()` / `run_next()` returns completed | Drain received data and continue |
| Invalid `step()` duration | Correct it and retry; this validation does not poison the controller |
| `submit()` overflows | Drain or reduce pending output, then retry |
| Protocol, transport, or receive-queue failure | Treat the controller as permanently failed; restart the session |
| `stop()` returns stopped | Shutdown succeeded; `ControllerResult::ok()` is false because it means completed |
| Timing failure or timeout | Simulator state may be unknown; arrange external termination if stop fails |

Guest controller failures are permanent, including transport errors encountered
during polling. Pending m5 operations can outlive a failed timing wait, so the
host retains its I/O worker until stop or destruction. Timing shutdown cannot
interrupt a running interval. The simulator's pseudo-ops can panic on socket
failure, and successful sends do not acknowledge application delivery.
