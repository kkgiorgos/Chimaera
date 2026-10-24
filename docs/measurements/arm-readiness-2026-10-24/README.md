# Arm feedback backlog recovery, 24 October 2026

Preparation `375741ed50784074951fcfcc7eb29eee` was running on two 3 GHz
Timing cores. It produced only two torque commands, ending at 68.433 ms.
After 0.6655 simulated seconds the latest command was 597 ms old; the arm
had drifted away from its ready pose. Joint feedback continued arriving,
but increasingly late, so the controller's unchanged 50 ms freshness check
correctly refused it. Host torque expiry remains 100 ms.

The transport selected the same destination locator once per reader. Composed
readers sharing a participant/port therefore caused duplicate sends and DDS
reception work. Locator selection now deduplicates complete destination
addresses and ports, while retaining separate participants/ports and targeted
retransmissions. Two regressions fail against the old runtime and pass with
the correction, including real typed/serialized fanout to two readers.

Tracing also used stream/locale and JSON-object formatting per packet. Integer
conversion and direct escaped JSON construction preserve identities, timestamps,
fragment records and complete capture. A native 100,000-identity microbenchmark
fell from 79.3 to 13.8 ms; this is not a modeled-CPU timing measurement.

Those changes alone did not remove the detailed-run backlog. Explicit
latest-state routes now coalesce superseded complete best-effort samples
within an already-arrived RX batch, before DDS dispatch. Reliable, durable,
grouped, image/bulk, fragmented and mixed control/data traffic is excluded.
All physical loans are released, and every arrival remains traced. Receive
rows expose batch size, route eligibility, parser eligibility and dispatch
choice, so skipped work is visible rather than presented as missing transport.

All **14 DDS CTest targets pass**. Checks include real small Fast DDS packets,
fragmented traffic, independent destinations, malformed/control exclusions,
route/QoS policy, endpoint disposal and transport backpressure. One earlier
full-suite run had a graph-readiness failure in the existing integration test;
its focused rerun and subsequent complete suites passed.

A short diagnostic using the original disk/kernel and application bundle with
the rebuilt DDS library reached arm and camera readiness at **0.3065 simulated
seconds** on two KVM cores. It delivered 148 commands through 0.768 seconds;
the last recorded control used 7 ms old feedback. The trace records 294
superseded state samples out of 730 latest-route arrivals. The device's startup
FIFO backlog drained, the throw was released and control continued. The run was
stopped after establishing readiness and control continuity; it does not
qualify a catch outcome.

**Detailed Timing CPU readiness is not yet qualified by this diagnostic.**
The original rates, hardware, physics, controller freshness limit and readiness
tolerances remain selected for the replacement production preparation.
Early detailed diagnostic attempts are retained under the ignored directory
`.chimaera-m5/arm-readiness-20261024/`; they were stopped before demonstrating
readiness. The replacement must be checked after its new checkpoint capture.

Small raw logs and native validation are in [evidence.tar.gz](evidence.tar.gz).
Hashes and scope are recorded in [validation.json](validation.json).
