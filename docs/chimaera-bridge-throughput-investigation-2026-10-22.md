# Bridge throughput and latency investigation, 2026-10-22

The best next experiment is **independent control and bulk service from the DDS
transport through to guest delivery**, followed by a registered-buffer m5ops
backend that feeds the participant directly. More buffer slots, larger messages,
or additional bridge threads alone do not remove the current blocking points.

Specialized m5ops are a plausible way to remove modeled payload DMA cost. They
also offer a way around the guest coordinator/router handoff. They need an
explicit Linux wakeup mechanism and bounded host work; the existing synchronous,
socket-based send/receive m5ops are unsuitable as their foundation.

This is a source investigation and structural diagnostic against
`912ae01ec5b1f3bec0ef4427b8756d0a160b9f43`. No new throughput ceiling or latency
improvement was measured. Production transport code is unchanged. The
[diagnostic results](measurements/chimaera-bridge-throughput-2026-10-22/structural-probe.json)
and [reproduction instructions](../profiling/chimaera_rewrite/head_of_line/README.md)
are retained alongside this proposal.

## Existing capabilities and blocking points

The custom transport carries **RTPS packets**, rather than typed ROS messages.
Host and guest coordinators control cross-simulator admission; ordinary DDS
serialization, reliability, histories and application callbacks remain active.

| Layer | Existing capability | Remaining serialization |
| --- | --- | --- |
| DDS participant send | Header/payload scatter-gather socket write | One `fd_` and `send_mutex_` per transport instance; lock covers a complete packet and a blocked send. |
| DDS participant receive | Registered locator channels | One socket reader calls `OnDataReceived` synchronously before reading the next packet, across all channels. |
| DDS router | Nonblocking independent clients, shared packet ownership, immediate admitted-packet egress | Each client's stream/output queue is FIFO. One worker/global mutex services clients; input drains until `EAGAIN`, and worker output has a potentially large budget. |
| Controller queues | Bounded queues; adjacent small-packet batching | One FIFO; `dma::layout` never skips a large head to batch later controls. DDS mode supplies no route coalescing key. |
| Host mailboxes | Sixteen slots in the integrated configuration | SPSC FIFO rings with one producer cursor/consumer cursor. Slots increase buffering, rather than create independent lanes. |
| DMA device | Separate active TX and RX state; multiple posted RX buffers | Exactly one `Transfer tx, rx`. RX also waits for acknowledgement of the previous completion; TX cannot accept another active transfer. |
| Guest DMA controller | Registered buffers and retained RX loans | One `optional<Message> active` for TX; later messages wait for its completion. More TX slots do not change this. |
| gem5 DMA port | Multiple outstanding memory responses | One shared `transmitList`; requests issue from its front. Chimaera enqueues every page of a bulk payload in `segmentsDone()`. |
| Application | DDS history and ROS callback dispatch | A shared reader/executor or expensive image callback can still delay controls after transport completion. |

Relevant implementations are
[participant transport](../ros/chimaera_ros_ws/src/chimaera_dds_transport/src/transport.cpp),
[router](../ros/chimaera_ros_ws/src/chimaera_dds_transport/src/router.cpp),
[outgoing queue](../gem5-transport/include/chimaera/outgoing_queue.hpp),
[batch layout](../gem5-transport/src/dma_protocol.hpp),
[mailbox](../gem5/include/gem5/chimaera_mailbox.hh),
[device](../gem5/src/chimaera/dma_device.cc),
[guest controller](../gem5-transport/src/dma_guest_controller.cpp) and
[DMA port](../gem5/src/dev/dma_device.cc).

Logical TX/RX overlap already exists. Both directions nevertheless share the
underlying DMA port and memory hierarchy, so a large transfer can also place
memory requests ahead of opposite-direction work. The magnitude requires a
detailed simulation; KVM does not establish that cost.

### What the structural probe establishes

The new probe compiles the **current production router**, queue, batch layout
and mailbox into an isolated executable. With a 1 MiB RTPS body and 64-byte
small bodies, it verifies:

1. A queued large message precedes later controls in both the controller and
   mailbox. The controls batch together only after the large head is removed.
2. A small packet on another input connection reaches the router while a bulk
   frame on the first connection is incomplete. Completing the first stream
   produces its bulk packet before the small packet following it.
3. An independent output recipient receives its small packet while a bulk
   recipient is deliberately unread. Small traffic sharing the bulk recipient's
   stream remains behind the entire bulk frame, even with a different locator.

Thus the router already has some independent-client progress. Simply adding
locator channels inside the same participant stream does not supply independent
service. The probe checks complete payload bytes and ordering. It does **not**
execute gem5, measure latency, or prove scheduling fairness under sustained load.
All nine existing transport tests also pass in the same SDK.

### Polling and large transfers already supported

DMA+Fast DDS currently blocks in `poll(..., -1)` on driver readiness plus an
eventfd. IRQs wake the driver wait queue, and the coordinator reads completions
directly. `has_work()` performs a readiness check, but there is no periodic
host-input polling timer in this path. The older `Gem5GuestController` still
exchanges input on a configured polling interval. These are different backends.

The custom DDS transport already accepts 1 MiB RTPS packets; DMA ABI 1 accepts
up to 32 MiB per descriptor, subject to the configured registered slot size
(8 MiB in recent experiments). The 64 KiB batch bound applies to **batches of
small packets**, not to all transfers. Local DDS transport settings can force
RTPS fragmentation; the local SHM profile explicitly avoids the smaller UDP
limit. The current approximately 518 KiB camera samples already cross in one
bulk DMA packet. Raising the DDS limit primarily helps larger future samples.
Changing it requires matching parser, receive buffers, SHM segment sizing,
registered slots, queue limits and tests, including framing overhead.

## What existing latency evidence implies

The [image pipeline profile](chimaera-dds-image-pipeline-profile-2026-10-21.md)
located 14.76 ms of a right image's 17.18 ms traced mean **after DMA completion**.
This was an older instrumented KVM implementation, before the latest copy and
scheduling changes. It identifies useful targets, rather than predicting the
current backend's remaining savings. KVM recorded effectively instantaneous
payload DMA, so replacing that payload engine alone cannot explain away those
post-completion intervals.

The [latest scheduling experiment](chimaera-dds-scheduling-2026-10-22.md) improved
stereo-pair mean from 22.039 to 19.758 ms, but p95 increased in every matched
repetition. Removing the completion worker alone was effectively neutral in its
separate experiment. Mean-only improvements therefore do not meet this task's
latency requirement. The
[earlier implementation comparison](chimaera-implementation-comparison.md)
explicitly reports finite-cohort delivery rates, not saturation throughput.

## Proposed common architecture

Use a descriptor/ring contract shared by two experimental payload engines:
modeled DMA and functional m5ops. This separates the benefits of independent
service, participant integration and changed memory-cost modeling.

Provide at least two independently provisioned lanes in **each direction**:
latency-sensitive traffic and bulk traffic. Keep protocol/discovery progress
capacity available even when images occupy every bulk slot. Each lane needs
its own descriptors, RX credits, completions and queue accounting. Include a
session generation, endpoint/flow identity, sequence, byte count, buffer handle
and simulation admission epoch in descriptors. A ring shared by multiple
participants must serialize producers correctly; the existing SPSC mailbox
cannot become multi-producer just by adding atomics around its cursors.

Independence must begin before a full image holds the participant send lock and
end after receive dispatch. Initially, separate local control/bulk sockets can
test this without replacing local IPC. Separate receiver service is also
required: two sockets drained by the same worker doing long synchronous RTPS
processing can still delay each other. The eventual direct m5ops backend can
remove the guest payload socket and router entirely, while retaining endpoint
registration/discovery and the host coordinator's causal admission role.

### Scheduling and isolation

- Preserve FIFO within each transport flow and explicitly declared dependency
  group. Permit independent flows to progress in a different global order.
- Give ready controls prompt service, while reserving a minimum bulk share to
  prevent starvation. Bound bytes **and** records processed per dispatch; a
  stream of tiny packets must not monopolize the worker either.
- Reserve control buffers, completion entries and wakeup capacity. A separate
  priority queue is ineffective when all shared RX slots are loaned to images.
- Admit one image as a large descriptor, but issue its underlying work in
  bounded quanta. Do not enqueue all bulk pages into the shared DMA port before
  rechecking controls. Multiple active transfers need independent completion
  identities and out-of-order completion handling in device, driver and library.
- Keep receiver loans until `OnDataReceived` returns; a future DDS loan API
  requires its own stronger ownership contract. A slow bulk consumer must not
  retain control credits. Retain bounded backpressure and explicit failures.

One large application transfer and interleaved internal service are compatible.
Avoid fragmenting every image into separately acknowledged DDS or guest-library
transactions just to gain a scheduling point. Prototype DMA quanta such as
4/16/64 KiB and tune using detailed measurements. A conditional service estimate
is `control wait <= residual issued bulk work + service/notification overhead`;
the residual includes all already-issued requests and shared-memory contention,
not just the newest chunk. This is not a hard real-time guarantee.

### Flow classification belongs near DDS endpoints

The current RMW hook already sees topic names and endpoint direction. Extend it
to bind endpoint/flow identity and lane policy into transport locators or an
explicit registration table. Current routed application endpoints use the same
custom locator port (`1`); classifying by that port or packet size alone cannot
distinguish image, effort, joint and clock policies.

RTPS can combine data and reliability metadata, or multiple writers, in a
packet. Keep those packets intact and conservatively preserve their ordering
dependencies. If strict isolation requires preventing cross-lane packet
aggregation, qualify the pinned Fast DDS/RMW writer-to-locator configuration
first. Do not assume the transport callback provides a ROS topic or a single
writer. DDS reliability, per-writer order, local fanout, graph traffic, discovery
and participant disposal must continue to work.

## Specialized m5ops backend

### The useful meaning of “free”

`TranslatingPortProxy::readBlob/writeBlob` use functional memory accesses. They
avoid the payload's timed DMA transactions; they still perform translations,
coherent functional memory access and host copying. Guest wrapper instructions,
bookkeeping, synchronization and wakeups remain. KVM address m5ops cause VM
exits. See the official [gem5 memory-system documentation](https://www.gem5.org/documentation/general_docs/memory_system/)
and [m5ops documentation](https://www.gem5.org/documentation/general_docs/m5ops/).

Consequently, call this a **functional payload transport**, with explicit
modeling semantics. It is suitable when bridge transfer is infrastructure to
exclude from the modeled computer's DMA/memory load. Retain the DMA backend
when those memory transactions and their contention are part of the experiment.
An improvement from removing that load is a changed model as well as a faster
implementation.

The current send/receive handlers allocate an intermediate buffer, connect a
new Unix socket for every call, perform blocking host I/O, and log every call.
The wrapper makes separate header/payload m5 calls and exchanges batch
manifests. Reusing this implementation would retain host blocking and polling.
New handlers should enqueue/consume ready data and return bounded status;
never wait on a host socket inside the simulator event queue.

### Proposed ABI, not an implementation

Only two explicitly reserved opcodes remain unused in this tree (`0x58`,
`0x59`). Use two versioned entry points with command enums rather than allocating
an opcode for every operation. Retain existing send/receive compatibility.

| Entry point / command | Proposed responsibility |
| --- | --- |
| `m5_chimaera_control(QUERY)` | ABI version, feature bits, maximum descriptors/bytes, functional vs timed mode. |
| `control(BIND/CLOSE)` | Session generation, participant/endpoint/flow registration, lane budgets, notification association and teardown. |
| `control(REGISTER/UNREGISTER)` | Stable, validated buffer handles, offsets and access permissions; no deregistration while loans/transfers exist. |
| `control(ARM)` | Atomically check completion generation and arm notification; return immediately if work is already pending. |
| `m5_chimaera_data(SUBMITV)` | Consume a bounded array of complete RTPS buffer descriptors; return accepted descriptor count/status without waiting for credits. |
| `data(POST_RX/REAP/RELEASE)` | Post lane-specific receive capacity, obtain ready completion descriptors in batches, and release retained buffers/credits. |

An API descriptor can reference one image in full, or an iovec of its already
serialized storage. Batching is immediate opportunistic batching, with both
descriptor and byte limits; do not introduce a fill timer. Return ownership
only for accepted descriptors and define partial acceptance, oversize, retry,
stale handle, reset and cancellation precisely.

For an initial bounded synchronous `SUBMITV`, copy from the current caller's
buffer before returning; Fast DDS can then reuse its send buffer. Deferred
copying needs an owned/pinned registration and a completion-based lifetime that
the transport callback actually honors. Host pointers and guest virtual
addresses are different address spaces; a borrowed guest buffer cannot simply
become a persistent host reference.

The guest participant's `SenderResource` calls this API directly. For incoming
data, the simulator fills registered participant RX buffers only after causal
admission. An awakened participant receiver reaps a bounded batch, calls the
existing `OnDataReceived` interface, then releases buffers. This removes the
payload path through the guest bridge process and its local router socket.
The [Fast DDS 2.6 transport API](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/transport/transport_api.html)
is the existing integration boundary; application message APIs need not change.
Direct calls target payload movement, not typed serialization or DDS histories.

Large functional copies also monopolize a simulator handler in host wall time.
Use bounded synchronous submissions initially. If async chunked functional
service is needed, register stable pages and yield through safe simulator
events, with an explicit completion tick/order. Do not claim interleaving inside
one uninterrupted bulk handler, or silently add timed DMA costs to a functional
mode. Check both host wall latency and guest-visible latency.

### Waking Linux without polling

The practical first design uses **m5ops for payloads and a notification-only
interrupt endpoint** for wakeups. Reuse the proven IRQ/waitqueue behavior while
removing payload DMA, and associate notifications with each registered endpoint
or lane. The simulator publishes complete RX records, then asserts notification.
The driver wakes the waiting participant through an fd. Level/persistent
generation semantics prevent lost wakeups if work arrives before the wait.
Coalesce already-pending notifications, with no intentional control delay.

`ARM(expected_generation)` plus driver wait must recheck shared readiness after
arming. Acknowledge only consumed generations; publish records before signaling
and acquire them before reading payloads. Include shutdown/error readiness so a
receiver cannot sleep forever. `REAP` is consumption on notification, not a
periodic timer poll.

This still has a small modeled notification device. A strictly device-free
backend needs a separately designed simulator-to-guest-kernel wakeup route.
Userspace `m5_quiesce` suspends a gem5 CPU thread context, not a Linux task; it
can stop unrelated tasks on that core. The repository already documents an
address-mode quiesce/TimingSimpleCPU fetch race. Host eventfds also cannot
directly wake a task in a separate guest kernel. A blocking host-side m5 handler
would prevent simulated progress. None is an adequate replacement for an
OS-aware wakeup contract.

### Memory, ordering and time contracts

Register and prefault writable private pages before measurement. Pin/validate
stable physical-page lists for async RX; never translate a process's saved
virtual address using another process's current `ThreadContext`. Retain mappings
until all outstanding references drain. Use the correct functional/coherent
proxy path; copying into raw RAM backing storage can miss dirty cache state.

New ops need explicit non-speculative/serialization behavior, compiler/CPU
ordering around rings, and O3 tests for older stores and younger loads. The
current x86 decoder gives memory-writing `chimaeraRecv` `IsSerializeAfter`;
new opcodes will not inherit that behavior automatically. Test instruction
triggers in detailed CPUs and `_addr` triggers in KVM separately.

Preserve paused-boundary host-to-guest admission and boundary-visible
guest-to-host export. A host I/O worker may prepare bytes early but must not
write guest memory or publish completions from its own wall-clock timing. With
multiple KVM event queues, coordinate publication using gem5's event-queue
rules. Do not inject data into a running guest from an arbitrary host thread.
Checkpoint/restart and CPU switching require explicit generation invalidation
and drain/rebind behavior; DMA currently rejects checkpoints.

## Experiments and acceptance gates

1. **Measure the actual capacity curve first.** Hold controls at fixed rate,
   sweep offered image byte rate and burst size, and place small messages just
   after bulk start in both directions. Test 64 B controls with 0.5/1/8 MiB bulk
   bodies, including sizes above today's DDS cap in the experimental backend.
   Include two independent participants and mixed traffic in one participant.
2. **Isolate lanes with existing DMA.** Split local IPC and queues, reserve
   credits, then implement concurrent device state/completion rings and bounded
   DMA issue. Compare independent service against the current code before
   attributing gains to m5ops. Include stalled receivers and exhausted bulk
   credits; controls must still progress.
3. **Add functional m5ops with the same lane/notification contract.** First
   compare payload engines with matching delivery topology; then connect the
   participant directly to isolate the benefit of removing the guest broker.
   Keep existing DMA and old m5 implementations as references.

Run KVM correctness/scheduling checks and **TimingSimpleCPU/O3 qualification**
separately, plus matched native-free/native-stepped controls. Keep CPU geometry,
coupling, QoS, sources and payload verification mode fixed within each paired
comparison. Do not reduce coupling merely to obtain a better latency number;
the earlier 1 ms KVM experiment accumulated backlog.

For each load cell record offered/admitted/delivered bytes and messages,
per-stream loss/duplicates/order, source-release lag, queue/credit high-water
marks, time behind bulk, outstanding bulk bytes, wakeups, guest CPU work and
wall seconds per simulated second. Sustainable goodput is the steady delivery
rate with bounded queue occupancy and age over time, rather than total receipts
during drain divided by source-window duration.

Use control p50/p95/p99, maximum and deadline misses, together with image age,
stereo completion and goodput. Run repeated paired sustained windows with enough
controls for tail estimates (target at least 10,000 per cell); retain censored
messages and failed attempts. Predeclare the control deadline and statistical
non-regression criterion. For a literal no-compromise target, require the upper
confidence bound on the candidate-minus-baseline p99 difference to be at most
zero at matched load, with no added deadline misses; finite tests cannot prove
an absolute worst-case bound. Also compare loaded controls to the corresponding
control-only run. Higher bulk goodput with worse control tails fails acceptance.

Verify image bytes separately from performance measurements, as the synthetic
verifier itself occupies the receiving executor. Exercise wake-before-arm,
arrival-during-wait, simultaneous lanes, partial submit, ring wrap, buffer loan
retention, capacity exhaustion, unregister/reset in flight, malformed descriptors
and shutdown. Include a negative control showing that the current implementation
holds a small same-stream transfer behind bulk. Keep local DDS delivery,
discovery, reliable retransmission and endpoint disposal in the matrix.

This investigation supports prototyping independent lanes and a DDS-direct
functional backend. It does not establish that either is already faster, or
that transport work alone determines callback latency.
