# Direct DDS functional m5ops transport

Implementation branch: `feat/chimaera-dds-m5ops`, based on `912ae01`.

The opt-in `m5ops` backend moves RTPS payloads directly between the guest's
Fast DDS transport and gem5. A small PCI function provides registration and
interrupt notifications to Linux; it issues no payload DMA. The host coordinator
retains paused-boundary admission and export.

The actual KVM lofted ball-catching experiment passed on 2026-10-22, with
480×360 stereo at 30 Hz, 250 Hz control, four KVM CPUs and no memory aggressor.
The ball was inside the cup at the deadline (`cup_capture`), with 0.5315 s
containment. Evidence is retained in
`.chimaera-m5/ball-kvm-r11/` and `.chimaera-m5/first-catch-pass.json`.
Transport tests (11), DDS tests (3), bridge Python tests (89, including a corrected
metadata expectation), and benchmark tooling tests (69 after the short-suite
extension) passed after that run.
The ball-catching workflow checks passed 69 tests, with two skips.

All eight comparison implementations subsequently passed fresh-boot KVM
qualification with payload verification and no missing, duplicate or reordered
receipts. The main initialized-buffer identity is the prefaulting READY build
selected by the historical ball-catching records. The later unprefaulted FINAL
build is retained separately as a diagnostic: its default KVM run failed twice
while writing an unmapped receive buffer. READY binary identity is independently
pinned; exact correspondence to the retained source snapshot is unverified.

The approved short TimingSimpleCPU screen is complete: eight ball-like baselines
completed, as did the new backend's bidirectional 8 MiB case and two shared
native-free references. Historical scheduling DDS/DMA failed its bulk case with
a controller queue limit, leaving eleven of twelve total cases complete. No
stepped native case ran. The [comparison report](measurements/chimaera-dds-m5ops-2026-10-22/timing-short-results.md)
retains the failure, raw identities, latency, source counts and throughput
definitions. Earlier attempts and the scored r10 miss remain retained.

Against scheduling DDS/DMA, baseline median joint latency fell from 0.42 to
0.18 ms and clock latency from 0.47 to 0.26 ms. All eight baselines delivered
their measured stereo source cohorts at the configured load. This does not
establish higher maximum capacity or repeated tail non-regression.

Large-payload latency isolation remains unresolved. With bidirectional 8 MiB
traffic, the new backend's joint/clock maxima reached 123.38/127.41 ms and twelve
scheduled effort releases were absent from the source window. Independent
transport lanes do not isolate the endpoint's shared source loop and executor
or the DDS participant send critical section. Static source and raw event
ordering support those as coupling candidates; no per-stage trace attributes
the measured delay. The implementation therefore does not yet satisfy the
end-to-end no-added-control-latency target under this bulk workload.

## Configuration

Select `transport_backend: "m5ops"` together with
`middleware_transport: "fastdds"` in the session bridge settings. Set `m5_module`
to the notification module staged inside the guest and optionally set
`m5_device` (default `/dev/chimaera-m5`). Build `chimaera_m5.ko` against the
selected guest kernel's exact configuration and symbol versions. The managed
qualification kernel is `5.15.180-chimaera`.

The runner sets `CHIMAERA_DDS_BACKEND=m5ops` for participating processes on both
sides. Host participants use independent control/bulk Unix sockets. Guest
participants use two direct `M5Endpoint` instances, each with a driver fd,
registered RX pool, receive thread and persistent readiness. The guest Unix
socket carries endpoint/channel metadata only. Application message APIs,
typed serialization, DDS histories and reliability remain active.

Each routed topic gets a deterministic locator port derived from its canonical
wire topic. Image and CompressedImage routes default to bulk; other routes
default to control. Optional route `transport_class` selects `"control"` or
`"bulk"` explicitly. Collisions and conflicting wire-topic type/class bindings
fail configuration. Lane selection does not depend on packet size. Discovery
and graph traffic use control capacity.

Fresh boots are required. Checkpoint/reuse, hot removal, finite DDS
deadline/lifespan/liveliness policies, services/actions and external network
transport profiles require separate qualification.

KVM may execute past `workbegin` before its deferred global exit completes.
The coordinator therefore binds a temporary notification context before that
barrier and sleeps in `CHM5_IOC_WAIT_ADMISSION` afterward. The first paused host
ingress publishes a persistent epoch and wakes it by IRQ, after the host has
written the clock origin. It then reads the origin and closes the temporary
context. This startup handshake needs no receive polling or payload exchange.

## Payload and notification contracts

`m5_chimaera_control` and `m5_chimaera_data` use opcodes `0x58` and `0x59`.
Versioned commands bind a kernel-owned context, register/remove locator channels,
submit header/payload spans, reap completions, return loans and observe
persistent readiness. Generation and packet sequence identify ownership across
close/rebind and release. Submissions either synchronously accept the complete
frame or return recoverable backpressure; they never wait for a host socket.

The kernel pins ordinary WB pages and publishes validated physical page lists
to the model. The simulator does not retain a process's virtual address or
translate it after a Linux context switch. A receive `Message` retains its fd,
mapping and physical registration until the final loan is released, including
through synchronous `OnDataReceived`. Final file release synchronously removes
the physical registration before freeing pages.

The default pools are eight 64 KiB control slots and four 9 MiB bulk slots per
participant. The opt-in transport's RTPS cap is 9 MiB minus the 52-byte CDDS
header; the complete frame fits a bulk slot, including an 8 MiB application body
and serialization/RTPS framing. A large non-image route must explicitly select
bulk if its serialized frames exceed control capacity. The local SHM profile
must also have sufficient capacity and must not impose a smaller fragmentation
limit through another local transport.

Batch reap returns only packets already ready, without waiting for a batch to
fill. A shared batch loan holds its RX slots through all DDS callbacks; the last
view returns their credits in one validated operation. This reduces m5 call
overhead during bursts while leaving an isolated packet immediately eligible.

Readers first reap available work, then block in `poll` on their lane's driver
fd and cancellation eventfd. The driver installs the waiter before reading
persistent device status. Sticky IRQ acknowledgement cannot clear queued RX
readiness. No periodic receive polling timer or userspace CPU quiesce is used.
Unaccepted DATA at a full submit/transmit queue has datagram rejection
semantics. The pinned Fast DDS version ignores the transport callback result;
reliable RTPS recovery is separate, and best-effort samples may be lost under
pressure. Accepted frames retain their ownership until delivery or shutdown.
Setup may retry a device that the coordinator is still loading, for at most
30 seconds; that retry is outside normal payload/completion service.

The DDS shared library privately embeds PIC m5 wrappers/mapping. Linker symbol
hiding prevents the application clock plugin's mapping lifecycle from unmapping
the DDS transport's address trigger. Address m5ops work during KVM startup and
remain selected after a detailed CPU switch. New instruction-trigger operations
also declare ordering/serialization for detailed CPUs.

## Boundaries, ordering and capacity

Four SPSC raw-CDDS mailboxes separate control/bulk in both directions, with
16 slots per ring. The host writes input while paused, issues
`CHIMAERA_INGRESS`, then advances gem5. Once the interval completes,
`CHIMAERA_EGRESS` flushes accepted guest output into available ring slots;
the host drains and repeats until the bounded device queues are empty.
Egress flushing never admits host input or changes its admission epoch.

The host router accounts control and bulk credits independently in each
direction, including local recipient fanout. A full queue retains ownership
upstream and stops admission on that lane. Bridge/controller handoffs test
credits before removing a record. Under receive pressure, ring records and
bounded model output remain owned until the host consumer returns credits;
the other lane can continue at subsequent boundaries.
Host callbacks copy complete frames into independent bounded lane queues.
Dedicated writers hold ownership through partial socket writes and wait for
stream credit outside the DDS participant send lock. Queue limits include
in-flight frames; shutdown interrupts writers before releasing their storage.
Ordinary capacity pressure does not time out a partial frame and stop both lanes.

Guest-local DDS fanout is immediate within the guest. Cross-simulator input is
published only at admission boundaries, with explicit sequence and epoch.
Independent lanes may progress in a different global order; each routed flow
retains its packet order. Separate buffers and queue budgets prevent bulk loans
from consuming control RX capacity. The host acquires receive ownership before
returning a mailbox slot, including on capacity failures.

Paused IRQ publication holds and restores the current device event queue with
RAII. This is separate from pseudo-op dispatch's migration from the calling
CPU queue. Nested KVM interrupt wakeups must return a queue lock that the paused
operation actually owns, then release it before Python resumes simulation.

## Modeling and measurement

Payload accesses are functional/coherent. They do not generate timed payload
DMA transactions or model that DMA memory contention. They still perform host
copying and translation; KVM address triggers also cause VM exits. Guest
instructions, DDS work, notification handling and application callbacks remain.
A single large functional handler performs bounded but uninterrupted host work;
two lanes do not imply preemption inside that handler.

Keep the timed DMA backend for experiments where payload memory traffic belongs
to the modeled computer. Compare backends with the same CPU geometry, coupling,
application source behavior, QoS and verification mode, and report the changed
payload memory-cost model alongside latency/goodput.

The benchmark extension under [`profiling/chimaera_m5`](../profiling/chimaera_m5)
keeps the ball-catching stereo/joint/effort/clock traffic and adds independent
bidirectional bulk/burst and control-only reference workloads. Matched runs use
the same extended application endpoint while preserving each historical
implementation's host driver and guest bridge. Capacity results must use steady
receipt-time intervals and bounded backlog/age; receipts during drain divided
by the source duration are a separate cohort-delivery rate. Payload-byte checking
is a separate correctness workload. KVM iteration results and final established
TimingSimpleCPU measurements must remain distinct.
