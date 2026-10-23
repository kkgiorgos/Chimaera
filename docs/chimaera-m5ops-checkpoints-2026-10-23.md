# Composed DDS+m5ops ball catching and robot-ready checkpoints

The agreed integration uses two 3 GHz cores, 16 KiB L1I/L1D, 256 KiB L2 and
3 GiB guest RAM. The composed robot shares one ROS context/DDS participant and
uses separate effort and perception/planning executors. Functional middleware
copies are the target default; ordinary guest copies remain selectable.

The candidate configuration is
[`dds_m5ops_composed_two_lofts.json`](../orchestrator/experiments/dds_m5ops_composed_two_lofts.json).
The orchestrator default is activated only after robot and restore qualification.
Runtime evidence is retained under
`.chimaera-m5/orchestrator-checkpoints-20261023/`.

## Modeling boundary

`fixed.middleware_copy_mode` selects `functional` or `guest`. Both m5ops modes
stage the identical preload helper. It is enabled only in the robot command,
not host processes, the graph probe or metadata coordinator. Functional mode
excludes large scoped middleware copy/fill costs from modeled guest execution.
Application computation and other memory work remain modeled. This remains an
alternative memory-cost model, not a calibrated physical offload prediction.

`fixed.checkpoint_readiness_cpu` selects `kvm` or `detailed`. The user explicitly
approved KVM through arm/camera readiness, followed by a switch to the selected
detailed CPU before saving. Restorers contain only the detailed CPU graph and
need no `/dev/kvm`. The checkpoint metadata records the preparation CPU, and
case reports retain both preparation and measurement boundaries. KVM readiness
costs must not be reported as Timing/O3 application latency. Checkpoint reuse
saves preparation work; it does not accelerate the modeled throw itself.

## Saved and reconstructed state

The m5ops sidecar contains PCI/device context state, physical RX page lists,
ready and loaned slots, completion ordering, pending packets, admission and
notification counters, and unread external ring contents. Host pointers,
mailbox mappings and file descriptors are never retained. Restore attaches
fresh external rings. The versioned binary format checks queue and byte bounds,
slot ownership, unique pages, discovery identities and execution geometry.
The checkpoint index hashes the sidecar and every memory/disk overlay file.

Gazebo records exactly the effort and readiness values consumed at each physics
step, plus the received command state not yet consumed at the last boundary.
It uses existing consumers and adds no guest threads or DDS subscriptions.
Replay reconstructs this pre-throw physics prefix while gem5 remains paused.
The host verifies exact simulation time and joint positions/velocities against
the saved boundary, and requires the complete command prefix to be consumed.
The recorded prefix is not arbitrary mid-throw checkpoint support.

Restored guest participants initially remember old external DDS peers. The device
retains their SPDP identities and sequence numbers, then queues standard
SPDP dispose/unregister messages after the saved ingress prefix, ahead of fresh
host ingress. Direct m5ops selects a built-in metatraffic port and excludes ROS
graph port 1; a regression uses the actual saved channel set to check this.
Without this step, infinite discovery leases could retain unacknowledged records
and eventually block callbacks producing reliable benchmark records.
A native crash/restart fixture verifies removal of both publisher and reliable
reader matches and successful communication with a replacement participant.

Before saving, the composed application stops and joins both executors at a
callback boundary. It exports each benchmark stream's next sequence number;
coupled stepping continues until the host has received the exact complete
prefix. That prefix and the watermarks are hashed checkpoint assets. Restore
copies the prefix into the new record journal without changing sequence numbers.
After old peer-disposal callbacks return their receive loans and replacement
record consumers match, the application sends a protocol probe through each
existing record writer. The host confirms receipt from all four readers through
the timing coordinator/readfile channel before application callbacks resume.
The application also requires exactly one matched reader per benchmark writer,
as this workflow has one journal consumer. Retaining an old reliable consumer
therefore blocks readiness rather than eventually exhausting writer history.
Local publisher matching alone was insufficient: one retained restore lost its
first eight control records while the replacement reader was still discovering
the writer. Probes do not consume benchmark sequence numbers or enter its journal.
This preparation-only barrier adds no DDS endpoints or guest threads.
The m5ops coordinator uses `CTL_QUERY.error == EAGAIN` for this transient rebind
state. Normal ABI geometry queries and packet ownership remain unchanged.

After replay and clock alignment, both simulators step together until the host
receives a new control timestamp and a successfully processed stereo capture
newer than the saved guest time. Only then does it enable memory pressure, apply
the configured pressure warmup, and release the throw. Measurement statistics
reset at launch. The coordinator's checkpoint polling is disabled before saving,
so restored runs do not retain its readiness polling work.

## Portable orchestrator assets

Readiness transfer combines the exact backing disk, guest bundle, kernel/module
pair and checkpoint files in a sparse stream. Consumers validate both readiness
and kernel manifests before publishing imported assets. They reuse the producer's
bundle and checkpoint rather than preparing their own readiness state.

Hardware, application, camera/rate, copy-mode or readiness-CPU changes require a
new checkpoint. Duration, throw parameters and pressure may vary under the
existing aggressor-free readiness contract. Frozen existing experiments retain
their implementation. The legacy m5 workflow remains supported; DMA checkpointing
remains unsupported.

## Qualification

Validation runs have one absolute two-hour wall-time budget beginning at
2026-10-23 20:19:47 UTC, with 90 seconds reserved for cleanup. The earlier
fully detailed readiness diagnostic is retained as a superseded attempt.
No full Timing/O3 catch, restore speedup or maximum throughput is claimed before
its corresponding run completes. Final outcomes and any unrun cases are recorded
with the measurements rather than inferred from native tests.

The integration remains a candidate: **the existing orchestrator default was
not changed and no running orchestrator was redeployed**. The two-hour run
budget ended before a complete corrected restore and its repeat could qualify.
Owned validation containers were stopped within the cleanup reserve.

The final capture completed in 238.5 host seconds, with two Timing CPUs verified
before saving. Its restore ran for 405.7 host seconds before the absolute cutoff.
Physics replay/state comparison and clock alignment passed. All four replacement
record readers confirmed receipt; the single-reader gate released the application.
Resumed control timing sequences 64–67 and control record 10 continued the saved
prefix without gaps. Fresh stereo had not completed the reconnection gate, so
there was no throw release or completed smoke result. The second restore was not
started. This is **incomplete qualification**, not a passed restore.

Earlier unchanged robot computation completed two actual 1.5 simulated second
KVM catches. These establish functional robot behavior, not detailed CPU catch
timing. Native checks passed: 357 orchestrator/container pytest checks, 167 ROS
workflow checks (two skipped), nine transport tests, seven DDS tests, robot and
physics unit tests, and native launch/legacy checkpoint replay checks. The port
selection regression uses the saved channel set; the native DDS fixture does not
exercise the simulated device's selection logic.

Retained failures identified a missing telemetry prefix, an eight-record gap
after one-sided DDS matching, and disposal sent to ROS graph port 1. The code
preserves/drains the prefix, uses reader-confirmed probes, and selects built-in
metatraffic. The final fresh-stereo gate still needs investigation/qualification
before enabling the candidate. The two lofts' full Timing windows, O3 restore,
ordinary-copy restore and distributed worker execution remain unvalidated.

Machine-readable attempts, source/artifact hashes, configurations, records and
logs are in [the evidence directory](measurements/chimaera-m5ops-checkpoints-2026-10-23/qualification.json).
