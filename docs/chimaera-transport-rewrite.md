# Chimaera transport rewrite

Work branch: `feat/chimaera-low-latency-transport`.

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
directions; both rewrites 20/20. Native controls and full stream counts must
accompany these values; camera latency describes received frames. Six images
per camera cannot establish sustained throughput or stable tails. In particular,
the first rewrite's lower image means accompanied worse delivery completeness.

The original image run took 656 wall seconds, the first rewrite 512, and the
buffer iteration 552. Small-message wall times were 112, 142 and 140 seconds.
Host load was shared; these are exploratory timings, not isolated speed ratios.
The native gap includes detailed CPU versus physical CPU behavior as well as
the bridge. It is not an isolated measurement of transport CPU cost.

## Next architecture: coherent DMA and interrupts

The next prototype replaces guest polling and synchronous m5 payload transfers.
It is a separate backend until it has correctness and detailed-CPU evidence.

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
   construction should allocate from registered transmit buffers, avoiding a
   further image-sized staging copy.
5. Independent ROS readiness and device interrupts wake the bridge. No periodic
   receive sleep is required. Completed guest output becomes visible to the host
   at the completed simulation boundary, preserving the coupling contract.

Initial invariants: bounded memory; validated lengths and descriptor counts;
no partial message publication; FIFO unless coalescing is explicitly configured;
stereo atomicity; no buffer reuse before DMA completion; reset drains DMA before
unmapping; no guest execution to satisfy a host-side transfer fence. Fresh boots
are the first validation mode; checkpoint and reused-VM support require their
own device-state validation before being advertised.

## Validation and evidence

The first milestone passes five transport tests, twelve ROS/package tests,
AddressSanitizer/UndefinedBehaviorSanitizer transport checks, and a separate
borrowed-publication lifetime sanitizer check. Native image tests compare every
byte at 640×480 and 1280×960. Three comparison-tool tests reject missing native
controls, workload/host mismatch and zero-publication results.

`profiling/chimaera_rewrite/summarize.py` exports raw run summaries and CSV while
requiring matched native controls and checking active gem5 CPU provenance.
Raw cases, source snapshots, builds and commands are retained under
`.chimaera-rewrite/20261020` on `ubuntu-george` and copied locally for analysis.

An isolated clock experiment investigates calibrated high-resolution guest
timers. Any clock change needs per-core Linux-versus-gem5 time and wakeup probes
after the CPU switch. Successful boot or a higher reported resolution alone is
insufficient evidence of a valid time source.
