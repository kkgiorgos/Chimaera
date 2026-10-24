# Readiness regression investigation, 24 October 2026

The overnight experiment `52c15e35701e4bfbb5de0b3045db219e` at revision
`8b1899f` completed preparation. Its saved checkpoint explicitly records
`readiness_cpu: kvm`; both readiness flags became true at 0.5645 simulated
seconds. The experiment cases subsequently ran on the configured two 3 GHz
Timing cores. This distinction matters: successful KVM checkpoint creation did
not qualify cold application startup and readiness on Timing cores.

The current experiment `cce9067f68a44858b9155c6dad3ac867` uses detailed
readiness with the same hardware, rates and 50 ms control-feedback freshness
limit. It stopped producing effort after six commands, the last at 0.076639 s.
By shutdown, joint sample transport age had grown from 43.8 to 227.3 ms.
Perception continued processing; gem5 did not fail. Today's transport tracing
adds reception/export work to this path, unlike the overnight publication/take
traces. KVM versus Timing is also a configuration difference, not proof that a
particular source commit changed the selected readiness CPU.

Read-only inspection of the host-to-guest control mailbox found more than
23,000 transfers before one simulated second. Its retained slots were mostly
builtin RTPS HEARTBEATs and discovery DATA, while the return mailbox contained
repeated ACKNACKs. Host DDS retry timers advance in wall time while detailed
simulation takes seconds per interval. The existing host router combines
identical pending retries, but its entry disappears when a packet is admitted
to the next queue. The controller handoff previously discarded its key, so
retries accumulated again ahead of the 16-slot admission ring. This leaves retries
in downstream FIFOs competing with joint
feedback. Coalescing only the eight physical RX slots rarely finds two joint
samples together: the stalled run dispatched all 549 observed feedback arrivals.

The credit-based handoff now carries the existing discovery retry key into the
host controller. Pending replacements use the same lane-specific record/byte
limits, and unrelated traffic retains FIFO admission and refusal semantics.
The receive path also returns control-lane physical loans before DDS dispatch,
collecting a bounded snapshot across successive already-ready physical groups.
It never waits for future packets. The snapshot stops at eight reaps, 64 records
or 256 KiB, checked after each complete physical group; a group is never split
or dropped to enforce a threshold. It copies control frames into owned memory;
bulk retains its physical loans. Each frame retains its original reap time.

Within that snapshot, the existing explicit latest-state policy can see and
combine superseded joint samples. Semantically identical pending discovery
HEARTBEAT/ACKNACK retries retain their newest arrival. Changed ranges, bitmaps,
flags, participant identities, application reliability traffic, malformed
frames and DATA remain distinct. No decision survives a snapshot. Every
application sample arrival still has its transport trace, including samples
whose DDS dispatch is superseded.

Validation is recorded in `validation.json` and `evidence.tar.gz`. A first local
diagnostic accidentally used the stock host RMW overlay and is excluded from
simulation validation. The corrected launch explicitly sources the middleware
overlay before loading the modified transport.

The selected recovery restores KVM preparation and preserves Timing CPUs for
all eight measured cases. The corrected diagnostic reached both readiness flags
at **0.3465 simulated seconds**, delivered **69 control commands**, and completed
checkpoint capture. Native validation passed **9 transport + 15 DDS + 19 bridge
tests (43 total)**. The Timing restore check is recorded separately.

The restored guest used two `BaseTimingSimpleCPU` cores. Its reconnection gate
passed after 147 intervals (0.0735 simulated seconds), with fresh effort and
perception. The diagnostic was then stopped; it does not qualify a catch or a
complete measurement window. Exact counts, feedback ages and image processing
records are in `validation.json`, with source logs in `evidence.tar.gz`.
