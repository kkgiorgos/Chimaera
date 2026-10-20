# Integrated DMA evidence, ubuntu-george, 2026-10-20

See [the implementation and results report](../../chimaera-dma-integration.md).

- `acceptance/`: matched 2 s performance cases for m5 and DMA, each with native-free
  and native-stepped execution on the same physical machine. CSV contains stream
  measurements; JSON also contains complete-pair metrics. HTML is interactive.
- `validation/`: eighteen completed byte-integrity, large bidirectional and
  instrumented ROS cases, with matched native controls. Pixel scanning and
  instrumentation add guest work; these are separate from performance acceptance.
- `dds-experiment/`: six matched SHM message-limit performance cases, plus a
  nine-attempt diagnostic archive. Its README explains the large-image case's
  empty effort-source cohort and the explicit performance selection.
- `history/`: all development attempts, including failures, interruptions and
  cancellations. This archive does not claim matched performance comparisons.
- `profile-tty.json`, `profile-ram-v2.json`: individual DMA intervals and gated
  FIFO capture attribution. Only one measured capture in each has all stages.
  `profile-ram.json` preserves the earlier attempt with no exported bridge trace.
- `chimaera-image-trace.log`, `chimaera-trace-export.json` and
  `chimaera-guest-packages.txt`: successful RAM trace and export/version evidence.
- `dma-device.json`: final standalone detailed-CPU host-mailbox integrity/guard/
  reset probe, including its own same-host native reference. This excludes ROS.
- `verification.json`: commands, local test outcomes, configuration and hashes.
  Case matrices retain exact workloads and diagnostic settings.

A `complete` attempt finished its execution window and telemetry export.
Delivery, source counts, duplicates, ordering and delay still need inspection;
completion status alone does not establish losslessness or a latency target.
Native timestamps use 1 ms buckets; zero is the same bucket, not zero work.

Full raw directories, immutable runtime/bundle/source archives and failed-case
logs remain at `~/Chimaera/.chimaera-rewrite/20261020` on `ubuntu-george`.
Measurement runs use isolated containers. Full-system cases use fresh COW
boots with read-only base assets. The production latency service is outside
these test namespaces.
