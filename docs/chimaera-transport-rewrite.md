# Chimaera transport rewrite

Work branch: `feat/chimaera-low-latency-transport`.

The [integrated DMA validation record](chimaera-dma-integration.md) documents
the subsequent ROS integration, interrupt fix, matched image DDS configuration
and full-system acceptance method. The numbers below retain the earlier pilots
and device microbenchmarks as historical evidence.

The objective is the lowest practical guest-visible delay while preserving
causal coupling and detailed CPU/memory behavior. Native execution on the same
physical machine remains the reference. KVM is a smoke-test tool, not an
acceptance CPU. The deployed acceptance configuration is two TimingSimpleCPU
cores, 3 GHz, 16 KiB L1 instruction/data caches, 256 KiB L2, MESI_TWO_LEVEL,
2 ms synchronization and a previously requested 1 ms guest poll.

## First implemented milestone

- `CHIMCTR3` sends a small manifest followed by separately owned payload frames.
  Controller batching no longer copies an entire image into and out of a
  contiguous packet. Startup rejects older peers. Whole-batch validation and
  receive visibility preserve atomicity, FIFO and explicit coalescing policies.
- The guest can wait on DDS readiness before an exchange; queued output bypasses
  another polling sleep. Callback service recollects available work with a
  bounded number of passes. Calls remain on one application thread.
- `SerializedView` borrows the transport buffer during synchronous ROS
  publication and restores the original owning handle even when publish throws.
  Stereo frames retain their existing atomic transport representation.
- `Message` owns contiguous bytes with deep-copy semantics and cheap moves.
  Ordinary construction and resize initialize new bytes. Receive allocation
  skips a redundant image-sized memset. The guest still writes once per page
  before m5 reception: functional translation cannot service a Linux page fault,
  and shared zero pages must become private writable mappings first.
- The benchmark receiver uses an event-driven executor. Source scheduling stays
  on a timer; native controls use the same changed endpoint implementation.

This milestone retains the existing m5 functional-memory/coherence path. It does
not claim that host input has become interrupt-driven or that high-resolution
guest timers are working.

## Detailed-CPU pilot

`ubuntu-george`, isolated container, fresh boots, 0.05 s warmup, 0.2 s measurement,
0.15 s drain; identical physics, rates, payloads and QoS. Each implementation has
both native-free and native-stepped controls on that machine. The stereo workload
uses two 640×480 RGB cameras at 30 Hz plus joint/control/clock traffic.

| Implementation | Small H→G mean | Small G→H mean | Left image mean / receipts | Right image mean / receipts |
| --- | ---: | ---: | ---: | ---: |
| Deployed original | 6.67 ms | 5.81 ms | 46.70 ms / 6 of 6 | 59.71 ms / 5 of 6 |
| Streamed protocol, executor and borrowed publication | 4.91 ms | 4.66 ms | 17.56 ms / 5 of 6 | 23.18 ms / 4 of 6 |
| Receive/source buffer initialization optimized | 5.03 ms | 3.70 ms | 17.22 ms / 6 of 6 | 26.72 ms / 6 of 6 |

All small-message publications were received: original 20/19 in the two
directions; both rewrites 20/20. Native controls and full stream counts accompany these values; camera latency describes received frames. Six images
per camera cannot establish sustained throughput or stable tails. In particular,
the first rewrite's lower image means accompanied worse delivery completeness.

For the final buffer iteration, the matched native-free small-message means
were 0/0 ms (H→G/G→H), and native-stepped means were 0.10/0.05 ms. Native image
means were 0/0 ms free-running and 0/0.17 ms stepped, with all six frames per
camera received. These native values use a 1 ms logical physics clock: a zero
means arrival within the same clock bucket, not zero CPU or transport time.
The final detailed-CPU gaps to stepped native were approximately 4.93/3.65 ms
for small messages and 17.22/26.55 ms for left/right images.

The original image run took 656 wall seconds, the first rewrite 512, and the
buffer iteration 552. Small-message wall times were 112, 142 and 140 seconds.
Host load was shared; these are exploratory timings, not isolated speed ratios.
The native gap includes detailed CPU versus physical CPU behavior as well as
the bridge. It is not an isolated measurement of transport CPU cost.

## Coherent DMA and interrupts

The integrated `dma` backend replaces guest polling and synchronous m5 payload
transfers. The default remains `m5` so existing sessions retain their configured
transport. Both backends use the same ROS interfaces and causal boundaries.

1. Host and simulator share bounded, versioned mailboxes. The host prepares input
   while gem5 is paused. An explicit timing-server command admits that input at
   the boundary. Device execution never depends on an asynchronous host socket
   arrival while virtual time advances.
2. A PCI device fetches scatter/gather descriptors and transfers payloads using
   the normal timing DMA port connected to Ruby's coherent I/O path. Completion
   occurs after memory responses; direct RAM writes and functional shortcuts do
   not stand in for timed DMA.
3. A Linux PCI driver owns registered buffers, descriptor lifetime, completion
   interrupts, mmap and reset. Large buffers use page-sized coherent allocations
   so they do not require physically contiguous multi-megabyte allocations.
4. The guest bridge borrows completed receive buffers for ROS publication. A
   message retains its buffer until its last owner releases it. Outgoing frame
   construction allocates from registered transmit buffers, avoiding a
   further image-sized staging copy.
5. ROS readiness and device interrupts wake the bridge through one executor. No periodic
   receive sleep is required. Completed guest output becomes visible to the host
   at the completed simulation boundary, preserving the coupling contract.

Initial invariants: bounded memory; validated lengths and descriptor counts;
no partial message publication; FIFO unless coalescing is explicitly configured;
stereo atomicity; no buffer reuse before DMA completion; reset drains DMA before
unmapping; no guest execution to satisfy a host-side transfer fence. Fresh boots
are the first validation mode; checkpoint and reused-VM support require their
own device-state validation before being advertised.

The device, shared-mailbox ABI, Linux driver and standalone host/guest probes
are implemented. The driver allocates coherent pages, exposes scatter/gather
buffers, reports completions through interrupts and drains DMA on reset before
freeing pages. The guest probe verifies every byte, changes data between repeats,
resets an in-flight transfer, unmaps, closes and reopens. The host-mailbox probe
echoes only while gem5 is paused and explicitly admits input before resuming.
No guest polling sleep or socket worker performs device reception.

Pool-backed `Message` adoption and ROS readiness integration are implemented.
Controller protocol 2 batches consecutive small messages while retaining
separate image buffers and global FIFO. Bulk DMA can still delay a following
control packet; independent priority queues remain a possible follow-up after
measuring the integrated path. The model includes timing/coherent memory
traffic but does not impose a separate PCI link-rate limit.

### First detailed-CPU device measurements

These are **raw device round trips**, not end-to-end ROS latency. Each size has
two samples and byte-for-byte integrity checks. The matching native run performs
two in-process memory copies on `ubuntu-george`; it is a lower-cost physical CPU
reference, not an emulation of IRQ handling. Allocation, pattern checking and
the deliberately expensive reopen test are outside the measured interval.

With one outstanding coherent DMA cache-line request:

| Payload | Device round trip | Same-machine native copy reference |
| --- | ---: | ---: |
| 64 bytes | 6.73–21.65 µs | 0.031–0.174 µs |
| 4 KiB | 9.84–23.50 µs | 0.099–1.150 µs |
| Stereo 640×480 RGB, 1,843,200 bytes | 1.812–1.824 ms | 0.215–0.319 ms |
| Stereo 1280×960 RGB, 7,372,800 bytes | 7.361–7.367 ms | 1.060–1.075 ms |

Both detailed device loopback (`dma-timing-r7`) and actual host mailboxes
(`dma-mailbox-timing-r1`) passed all ten payload checks plus in-flight reset and
reopen. With 2 ms host admission boundaries, the mailbox control round trip was
1.22–1.99 ms and the stereo 640×480 payload round trip was 2.04–2.21 ms. These
include boundary waiting. KVM also passed integrity/reset checks and remains a
smoke test only.

Concurrent DMA exposed a real forward-progress failure in MESI_TWO_LEVEL.
After warming image data in CPU caches, eight outstanding DMA requests exhaust
L2 replacement TBEs. An invalidation stalled at the head of L2's response queue
then blocks the memory acknowledgements that would release those TBEs. The
retained protocol trace shows both `MEM_Inv M→M_I` and CPU `L1_GETS NP→ISS`
resource-stalling until the sequencer deadlock detector fires. A dedicated DMA
port does not resolve this. The fix recycles a DMA invalidation when
its transition needs an unavailable TBE, allowing acknowledgements to proceed.
Higher concurrency now passes the same aligned-image regression at eight
requests (`dma-timing-r9`), including reset/reopen.

Testing nonaligned lengths with dirty receive guards exposed a second coherence
bug: a partial DMA write to a CPU-owned line wrote only the DMA bytes back to
memory and lost neighboring dirty CPU bytes. The directory now merges the DMA
range into the authoritative invalidated cache line and writes the complete
line back. The final probe adds empty, 63-byte, 4097-byte and nonaligned stereo
payloads, adjacent dirty guards, exclusive-open and invalid-argument checks.
All eighteen byte/guard checks plus reset/reopen pass on the detailed CPU at
64 outstanding requests (`dma-timing-r12`), and on KVM (`dma-kvm-final`).

The final detailed-device means below each use two samples and their own
same-machine native reference. They remain microbenchmarks, not ROS callback
latency or sustained throughput acceptance:

| Payload | Coherent DMA round trip | Matched native copy reference |
| --- | ---: | ---: |
| 64 bytes | 8.18 µs | 0.026 µs |
| Stereo 640×480 RGB | 0.381 ms | 0.259 ms |
| Stereo-sized packet plus 137 metadata bytes | 0.381 ms | 0.216 ms |
| Stereo 1280×960 RGB | 1.477 ms | 1.023 ms |

The final host-mailbox test (`dma-mailbox-timing-final`) also passes all eighteen
payload/guard cases, invalid-input checks and reset/reopen. At 2 ms admission
boundaries its 64-byte round trip is 1.993 ms, stereo 640×480 is 2.073 ms, and the
7.37 MB payload is 2.57–2.69 ms. The difference from device loopback makes the
synchronization cost visible. These historical device measurements remain
separate from the integrated ROS validation described below.

Linux also needed readable local-APIC trigger-mode registers: gem5 already
maintains the bits, but previously aborted on reads during PCI interrupt
acknowledgement. The prototype's MP-table interrupt mapping uses `acpi=off`
because the minimal gem5 ACPI tables lack PCI routing. Production ACPI routing,
checkpoint/reused-VM support and stable long-run tail measurements remain
explicit follow-up work.

The integrated backend now supports paired native comparisons, full image-byte
verification and separate lossless/latest-frame policies. A further clock design should expose the
model's authoritative time to Linux through a paravirtual clock source and
appropriate clock events. It needs the per-core oracle and wakeup validation;
forcing a supposedly reliable TSC is not a validated fix. After measuring those
layers, synchronization can be reduced or made adaptive within the simulators'
causal lookahead, with the gap to native reported at each step.

## Validation and evidence

The integrated branch passes nine transport tests, fourteen ROS/package tests,
64 deployed-configuration checks, and all nine transport tests under
AddressSanitizer/UndefinedBehaviorSanitizer. Tests cover completion notification
before/during executor wait, held receive loans, transmit staging starvation,
shutdown with external loans, malformed peers and lossless stereo pairing.
Eight comparison/collector checks reject missing or mismatched native samples,
endpoint fingerprints, empty sources and incorrect detailed CPU provenance,
while preserving failed and cancelled attempts.

`profiling/chimaera_rewrite/summarize.py` exports raw run summaries and CSV while
requiring matched native controls and checking active gem5 CPU provenance.
Raw cases, source snapshots, builds and commands are retained under
`.chimaera-rewrite/20261020` on `ubuntu-george` and copied locally for analysis.

An isolated Atomic-boot clock experiment exceeded its 1800 s startup timeout
during guest setup, before the detailed CPU oracle ran. It did not validate a
clock fix. The production timer configuration is unchanged. Any clock change
needs per-core Linux-versus-gem5 time and wakeup probes
after the CPU switch. Successful boot or a higher reported resolution alone is
insufficient evidence of a valid time source.
