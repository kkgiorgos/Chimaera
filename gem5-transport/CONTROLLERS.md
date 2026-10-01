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
chimaera::Gem5HostController host(timing); // timing must outlive host
try {
    timing.wait_until_ready();
    for (int i = 0; i < 10; ++i) {
        host.submit(chimaera::Message{}); // example application message
        const auto result = host.step(std::chrono::milliseconds(100),
                                      std::chrono::milliseconds(10));
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
bootstrap and instruction ops after the first nonzero host epoch. Keep the
mapping alive throughout polling. A nonzero epoch is meaningful only when the
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
    Guest->>IO: Poll with outgoing batch
    IO-->>Guest: Reply with host batch, epoch, poll duration
    Note over Guest: Deliver messages; next call waits on guest OS clock
    Note over Time: Pause when interval ends
    Time-->>App: OK start_tick end_tick
    Note over App: Snapshot decoded guest batches; take() delivers them
```

A guest `run_next()` performs one exchange. Before the next exchange it sleeps
for the previous reply's polling duration, using the simulated guest OS clock.
Returning before that sleep lets the application deliver received messages and
run callbacks promptly. Actual spacing also includes guest scheduling and
application work; the duration is not a guarantee of a fixed poll count.

An empty reply is valid, so polling never waits for application data. Before the
first host step, replies carry epoch zero and no application data. The guest
keeps bootstrap polls inside `run_next()` until a nonzero epoch arrives, retaining
submitted data. This protects the window in which KVM executes beyond workbegin
before gem5 services its global exit.

## What an interval boundary guarantees

After gem5 confirms its pause, `step()` exposes the current snapshot of fully
decoded guest batches through `take()`. A transfer or worker decoding can cross
the pause. Such data becomes available at a later boundary; no partial message
is delivered. The controller never resumes gem5 just to finish a transfer and
never joins its I/O worker at a boundary.

A prepared reply can carry the preceding epoch or polling settings. The guest
picks up new settings on a later poll. Poll more frequently than the synchronization
interval to reduce that delay. An interval is a simulation pause and receive
snapshot, not an acknowledgment that every queued message reached its application.

Each controller queue and packet batch is capped at 1024 messages and 32 MiB of
payload. Unconsumed received data counts toward the queue limits. The worker can
also hold a detached batch in flight; these limits are not one global memory cap.
Submitted data leaves the queue when a packet is prepared. Failure afterward
does not requeue it automatically.

The host swaps outgoing queues and incoming snapshots under a mutex, then
assembles packets and merges received data outside it. Preallocated incoming
descriptors avoid allocation while publishing under the mutex. Payload buffers
move without copying their bytes. The receive cursor avoids shifting every
remaining descriptor on each `take()` and reclaims consumed slots on append.
These choices keep the worker from delaying the application at a boundary.

## Timing and the two clocks

`Gem5TimingController::start(interval)` sends a command and returns;
`wait()` reads completion once the simulator pauses. Only one timing request may
be outstanding, and only this timing session may advance that simulator.
Intervals must be positive and at most one simulated hour. Host `step()` also
requires `0 < poll <= interval` and at most 100000 nominal polls per interval.

The simulator uses a fixed 1 THz tick frequency: one nanosecond equals 1000 ticks.
The controller anchors an ideal cumulative target at the first step's start tick.
Each subsequent interval adds to that target, then requests
`max(0, target_tick - last_actual_tick)`. If a nominal 100 ms step advances 103 ms,
the next 100 ms interval requests 97 ms. Large overshoots can require zero-tick
steps while the ideal target catches up. Requests are capped at one simulated
hour; remaining undershoot carries forward. Integer tick accounting preserves
sub-nanosecond drift. This corrects cumulative progress, not the precision of
individual pause boundaries.

`WallClockPacer` addresses a separate problem: limiting simulation progress per
real second. Construct it after startup, pass actual `timing.elapsed_ticks()` to
`delay()`, and sleep or service input before checking again. The delay is capped
at one wall second. `report()` returns recent and overall achieved ratios.
A ratio of 1 targets real time; 0.5 targets half speed; 2 targets twice real time.
Cumulative pacing avoids accumulating oversleep. When behind, it requests no
wait; it cannot make the simulator run faster. See the
[talker/listener host loop](../ros/talker_listener_ws/src/talker_listener_bridge/src/host_bridge.cpp)
for integration.

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

Controller packets travel inside the [raw transport framing](README.md).
[src/controller_protocol.hpp](src/controller_protocol.hpp) defines their current
format: five big-endian uint64 fields followed by length-prefixed messages.

| Field | Poll request | Host reply |
| --- | --- | --- |
| Magic | `CHIMCTR1` | `CHIMCTR1` |
| Kind | 1 | 2 |
| Epoch | Last observed epoch, initially zero; currently ignored by host | Host interval number; zero means bootstrap |
| Poll duration | Zero; currently ignored by host | Guest waiting duration in nanoseconds |
| Message count | Number of outgoing messages | Number of outgoing messages |

Each message has an eight-byte big-endian length followed by its bytes.
Decoding rejects mismatched magic/kind, truncation, trailing bytes, excessive
counts/payload, and polling durations above one hour. The guest also rejects
nonpositive reply durations, epoch regression, and application data in bootstrap
replies. This documents the existing protocol; directional packet refactoring
is deferred.

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
