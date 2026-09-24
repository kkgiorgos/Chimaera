# gem5 controllers

The self-contained `chimaera` controller interface is in
[`include/chimaera/controller.hpp`](include/chimaera/controller.hpp).
It retains the producer, consumer, timing, and host/guest controller contracts.
The implementation uses the real gem5 transport and has no build or runtime
dependency on `mock-sim`. Gazebo is not connected yet.

## Build, deploy, and run

From the repository root:

```sh
make -C gem5-transport
# With gem5/QEMU stopped:
make -C gem5-transport deploy-controller
```

Deployment uses the same `IMAGE` and `PARTITION` settings as `make deploy` and
installs `/usr/local/bin/gem5_controller_guest`. The original raw transport
examples and `make deploy` remain available separately.

Start the host first, in one terminal:

```sh
./gem5-transport/build/gem5_controller_host --ratio 1 --interval-us 100000 --poll-us 10000
```

In another terminal:

```sh
gem5/build/X86/gem5.opt --outdir=m5out-controller \
    gem5/configs/custom/x86-ubuntu-socket-server.py --boot-to-controller
```

This boots the guest, launches the deployed controller through the image's
readfile startup script, and pauses at its `m5_work_begin_addr` marker. At that
marker it resets statistics but keeps running on KVM. Both boot and controller
intervals use KVM, with perf counters disabled. If the guest asks for a sudo
password, connect to its serial console (normally port 3456) and use the guest's
credentials. The controller needs access to the m5 mapping device or `/dev/mem`.
The host waits for the timing socket, then advances automatically. No manual
`step` commands are needed. Its default startup timeout is 300 wall seconds;
change it with `--startup-timeout N` if boot or authentication takes longer.

`--ratio R` means **simulated seconds per wall-clock second**: `1` targets real
time, `0.5` targets half speed, and `2` targets twice real time. `--interval-us`
is the nominal simulated sync interval in microseconds and `--poll-us` is the guest polling
interval, also in microseconds. Both retain the tick-drift compensation described below.

While running, use these host console commands:

```text
send hello from the host
status
quit
```

`send` alone queues an empty message. `status` refreshes the achieved rate now.
Send/receive logs show the peer, byte count, and message content.
Connect to gem5's serial console (normally port 3456) and type `send TEXT` to
queue guest data. Input is handled between intervals and during pacing waits;
it never blocks simulation advancement waiting for a line of text. EOF disables
console input but lets automatic execution continue, so redirected/headless
runs work. Use `--steps N` for a bounded run, or `quit` / Ctrl+C to stop.
A stop signal is handled after the current simulation interval completes.

`quit` shuts down gem5 through its timing socket without executing another
guest instruction. It then cancels and joins the host I/O worker. Exit normally
to remove socket files; stale paths after forced termination must be checked
and removed as described in the transport README. Do not run raw transport
examples or legacy bridges on the same socket paths concurrently.

Without `--boot-to-controller`, gem5 opens the timing socket at tick zero and
waits for steps; boot time is then included in those steps. This mode also
supports timing-only use. The host controller does not require a guest handshake
before advancing, and queued messages remain buffered until a guest polls.

## Wall-clock pacing and achieved rate

The host measures elapsed wall time with `std::chrono::steady_clock`, starting
after gem5 is ready. Boot/startup is excluded when using `--boot-to-controller`.
Rate measurements use the **actual tick progress** returned by gem5, not the
number of requested intervals. They include socket, callback, console, and
pacing overhead during the measured run.

When ahead of the target ratio, the host waits until
`wall_elapsed >= actual_simulated_seconds / ratio`. This is a cumulative
schedule, so oversleep does not accumulate as additional per-step delay.
When behind, it runs the next interval immediately. It preserves sync interval
sizes and cannot force gem5 to run faster than the hardware allows. KVM remains
the only CPU model. Pacing controls the average rate; execution still happens
in interval-sized bursts, so smaller intervals improve smoothness at the cost
of additional overhead.

The host refreshes a fixed bottom status bar every wall second by default
(`--report-seconds S`). Messages and console input stay above it; updates do
not add lines to the message history. Redirected output and terminals without
ANSI support omit periodic reports; `status` and the final summary still print.

```text
Rate: target=1x achieved=0.98x average=0.99x sim=9.9s wall=10s
```

`achieved` measures actual simulated seconds / wall second since the preceding
report; `average` covers the whole run. The bar is removed on exit, and a final report prints the overall rate
before shutdown. Reports and console input are serviced while pacing and at
interval boundaries. If a gem5 step itself takes a long time, the next report
arrives when that step completes. A requested rate that cannot be reached is
therefore visible in the achieved/average values, rather than reported as met.

For example, run 50 nominal 20 ms intervals at half speed:

```sh
./gem5-transport/build/gem5_controller_host --ratio 0.5 \
    --interval-us 20000 --poll-us 2000 --report-seconds 0.5 --steps 50
```

## Application interface

Include `chimaera/gem5_controller.hpp`. Link host code to
`chimaera::host_controller` and guest code to `chimaera::guest_controller`.

```cpp
chimaera::Gem5TimingController timing("/tmp/chimaera_time.sock");
chimaera::Gem5HostController host(timing, producer, consumer);
auto result = host.step(std::chrono::microseconds(100000),
                        std::chrono::microseconds(10000));
// Check result.state / result.message; call host.stop() for explicit shutdown.
```

Applications implementing their own run loop can call
`timing.wait_until_ready()` before pacing, use `timing.elapsed_ticks()` for
actual progress, and use `chimaera::WallClockPacer` from
`chimaera/wall_clock_pacer.hpp` (also in `chimaera::host_controller`). Its
`delay(ticks)` returns a bounded remaining wait; recheck it after sleeping or
servicing input. `report(ticks)` returns recent and overall achieved rates.
Construct the pacer after startup and before the first step. The existing
`step()` interface remains available for applications that schedule it themselves.

`Gem5GuestController(producer, consumer)` owns a `GuestTransport`. Keep calling
`run_next()` while it returns completed. The application must map libm5 before
using it. For boot-to-controller startup, issue `m5_work_begin_addr(0, 0)` after
constructing the controller and before calling `run_next()`, as the example
does. `run_next()` detects host interval numbers through poll replies: its
completion is observed on a later interval's resume, not while paused. If a
short interval contains no guest poll, the next observed interval number can
skip ahead. Shutdown terminates the simulator, so guest code cannot observe a
`stopped` return after host `stop()`.

Producers and consumers are application-owned and must outlive their
controllers. Calls into each controller must be serialized. All callbacks run
on that controller's calling thread; none run on the host I/O worker.

## Buffering and pause boundaries

The underlying m5 operations are synchronous and open a fresh host socket per
call. The host therefore services them on a worker thread while the caller
waits for gem5 to finish its interval. A guest poll sends a batch and receives
a reply containing host data, an interval number, and the polling duration.
An empty batch is a valid no-data reply, so polling never waits for application
data. Guest sleeps use the simulated guest OS clock rather than host wall time.
The effective poll spacing includes guest scheduling and callback execution.

Startup polls carry no application data until the first host interval is
observed. This also covers the small window in which KVM executes past the
workbegin marker while gem5 handles the global exit; no producer or consumer is called during that bootstrap window.

Before each step, the host collects producer data and queues it for future
polls. After the timing server confirms the pause, it delivers the current
snapshot of fully decoded guest batches to its consumer. A transfer can span
a pause: partially transferred or not-yet-decoded batches remain pending and
are delivered at a subsequent boundary. No partial application message is
delivered, and the controller never resumes gem5 just to finish a transfer.
An already prepared poll reply may contain the previous interval number; the
guest picks up updated settings on a later poll. Keep polling intervals smaller
than synchronization intervals to reduce this delay.

Each queued direction and each packet batch is limited to 1024 messages and
32 MiB of payload. Overflow reports failure instead of silently dropping data.
An additional batch can be in flight in the worker. Producer `take()` removes
data; a later failure does not automatically requeue it. Protocol and transport
failures make the controller unusable for further steps.

`HostTransport::cancel()` is the only transport method safe to call concurrently
with send/receive. It wakes blocked socket operations permanently. The controller
uses it to join its worker even if gem5 is paused halfway through a transfer.
Destroying a host controller only tears down local I/O; use `stop()` first to
terminate gem5 cleanly. On a failed timing connection, simulation state may be
unknown; terminate/restart the session rather than retrying an interval.

## Timing socket

The Python configuration is the server. `Gem5TimingController::start()` connects
and writes a command, then returns without waiting for the simulation to finish.
`wait()` reads and validates the completion response. Only one request can be
outstanding. There is no busy polling and no Gazebo request. The default wall
timeout is 300 seconds, configurable in the timing controller constructor.

The protocol is ASCII, one newline-terminated command per connection:

| Command | Response |
| --- | --- |
| `STEP_US n` | `OK actual_start_tick actual_end_tick` after requesting n microseconds |
| `STEP_NS n` | `OK actual_start_tick actual_end_tick` after requesting n nanoseconds |
| `STEP_TICKS n` | `OK actual_start_tick actual_end_tick` after requesting n ticks (zero keeps gem5 paused) |
| `STATUS` | `PAUSED tick` or `DONE tick` |
| `QUIT` | `BYE`, followed by simulator shutdown |

The configuration fixes the tick frequency at 1 THz. Nominal intervals must be
positive and at most one simulated hour. Raw `STEP_TICKS` also accepts zero.
Invalid commands return `ERROR text`.
Workend or another terminating event returns `DONE tick`; C++ `wait()` treats
early termination as an incomplete interval. Intermediate boot exits/workbegin
events do not reset the requested budget. The server reports the actual start
and end ticks, even when KVM runs longer or shorter than requested.

The C++ timing controller maintains an ideal cumulative target, anchored at the
first response's start tick. Each `start(interval)` adds the nominal interval to
that target and requests `max(0, target - last_actual_tick)` ticks. For example,
if a 100 ms interval advances 103 ms, the next nominal 100 ms interval requests
97 ms; an advance of 98 ms would instead produce a 102 ms request next time.
Accounting is in integer ticks, so fractional-nanosecond drift is preserved.
If an overshoot spans multiple intervals, zero-tick steps let the ideal timeline
catch up without advancing gem5. Requests remain capped at one simulated hour;
any remaining lag carries forward. This corrects cumulative drift, not individual
boundary precision, and does not assume KVM can hit every requested tick.
Only this timing controller may advance its simulator during a session.

The server runs simulation on its main thread and replies only once it is
paused. Another request, including `STATUS` or `QUIT`, cannot interrupt a
currently executing step. Socket disconnects or timeouts do not undo a step.
The previously advertised JSON/ADVANCE commands are replaced by this explicit
protocol; the legacy ROS time bridge is not its client.

For another timing path, pass `--socket-path PATH` to gem5 and `PATH` to
`gem5_controller_host`. Data socket paths still match the constants in gem5's
existing pseudo-ops. Those pseudo-ops still panic on socket errors; this layer
cannot turn such a simulator panic into a recoverable guest result.

Simulation intervals and guest polling accept integer microseconds (minimum
1 us), for example `--interval-us 100 --poll-us 10`. Internal durations remain
nanoseconds and gem5 requests remain integer ticks to preserve drift correction
precision. Console pacing uses microsecond waits rather than rounding up to
milliseconds. OS scheduling and KVM exits can still overshoot these intervals.
Wall-clock startup timeouts and reporting cadence remain in seconds.
