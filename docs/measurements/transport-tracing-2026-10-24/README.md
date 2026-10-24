# Transport tracing validation, 24 October 2026

The previous publication-to-RMW-take measurement included serialization,
deserialization and executor waiting. It could therefore report tens of
milliseconds while perception occupied the executor, even after the transport
had delivered the image. Those recordings cannot recover an arrival timestamp.

The new Fast DDS trace records accepted serialized RTPS send entry and complete
sample arrival before DDS delivery. It joins writer GUID/sequence identities
and handles fragments without inspecting application payloads. Per-topic
transport statistics, publication-to-take delivery, and the stages around
transport are separate. Missing events do not become zero latency.

Transport queues, synchronization, worker scheduling and fragment pacing are
included. This is transport runtime residence, not an isolated wire/device
service measurement. Host clocks retain synchronization-boundary resolution.
See the [measurement contract](../../chimaera-tracing.md).

## Native validation

- Nine DDS CTest targets passed, including packet parsing, real fragmented
  typed/serialized traffic, transport backpressure and endpoint disposal.
- The new integration check sent three 2 MiB images through separate real ROS
  processes and the custom transport. All three complete fragment sets arrived
  while the receiving executor was deliberately unserviced. Advancing the test's
  shared logical clock by 100 ms before permitting takes changed delivery
  timestamps while transport timestamps retained their earlier values.
  This is a boundary regression, not a transport performance measurement.
- Nineteen bridge/benchmark CTest targets passed, including the optional native
  traffic test. That best-effort test initially missed its right-camera stream
  under default local DDS settings; its isolated rerun passed. No assertions or
  QoS were relaxed to make it pass.
- Python workflows: **303 passed, 2 skipped**. Focused checks after the final
  report/export changes: **42 passed** (a subset, not additional coverage).
- Browser checks used explicitly synthetic report fixtures. Transport displayed
  1 ms while publication-to-take displayed 30 ms for the same sample. An older
  recording displayed unavailable transport, retained its 42 ms delivery value,
  and produced no transport plot points.

Parser/analyzer regressions cover both byte orders, source-prefix changes,
truncation, discovery filtering, out-of-order fragments, retransmissions,
incomplete fanout receivers, dropped DDS history samples, same-process take
matching, negative clocks, and source-window versus receipt-drain censoring.

## Real guest validation

A fresh KVM guest exercised m5ops Fast DDS in both directions using the managed
kernel/module and read-only guest disk from the robot experiments. The DDS and
bridge packages were rebuilt into a dedicated overlay on cached application
image `sha256:d5a0abdd6279fc631280ad266f91742be0fecf6c6f3f438b86f012670559e0fc`.

Configuration: two KVM cores, 518,400-byte synthetic payloads at 30 Hz per
direction, 0.5 ms synchronization/physics intervals, 100 us poll setting,
100 ms warmup, 100 ms source measurement, 50 ms receipt drain, and 64 MiB DDS
shared memory. The source window contained three samples per direction. Both
directions recorded all three transport arrivals and all three RMW takes, with
zero negative latency or invalid clock events. End-to-end validation took
65.97 wall seconds.

Mean transport residence was 0.525 ms host-to-guest and 0.311 ms guest-to-host.
The corresponding publication-to-take means were 2.667 ms and 1.042 ms. These
six samples validate the clocks and measurement stages. They do not qualify
tail latency, model detailed-CPU execution, or establish robot performance.

The integration run also caught and fixed missing trace path fields in the
benchmark's guest manifest. Its staged bundle now advertises transport trace
ABI 1 and explicitly includes the address-mode gem5 clock. Older runtimes and
bundles fail before a trace-enabled run rather than silently returning only
delivery measurements.

## Collection and rollout

The robot and synthetic benchmark now use the same analyzer and transport
boundaries. Synthetic runs opt in with `--trace`; their report includes a
separate transport table and `transport-latency-report.csv`. Robot reports
select transport or delivery explicitly, including for older result files.

Source updates are synchronized to the controller and both workers. New
preparations rebuild the SDK/application and create new readiness checkpoints.
The active frozen recovery experiment continues with its saved runtime; its
records contain the original delivery measurements.

Selected raw traces, configurations, results, native test output and the exact
validation script are retained in `evidence.tar.gz`. Checksums and structured
metrics are in `validation.json`. Large bundles and builds remain under the
ignored `.chimaera-m5/transport-tracing-20261024/` directory.
