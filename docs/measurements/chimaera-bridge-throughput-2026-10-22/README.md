# Bridge concurrency investigation evidence

This directory contains structural checks, not simulation performance results.
The source investigation and proposed experiments are in
[`../../chimaera-bridge-throughput-investigation-2026-10-22.md`](../../chimaera-bridge-throughput-investigation-2026-10-22.md).

- `structural-probe.json`: successful production-router/queue/mailbox diagnostic.
  Independent sockets/recipients progress while a bulk stream is incomplete or
  unread; each individual stream, controller queue and mailbox retains FIFO.
- `probe-build.log`: Release build using the current production router.
- `transport-build.log`, `transport-tests.log`: all nine transport tests pass.
- `provenance.json`: base source revision, SDK image digest, source/artifact
  hashes and setup-attempt outcomes.
- `architecture.html`: self-contained interactive architecture comparison;
  proposed modes are explicitly distinguished from verified current behavior.

The diagnostic uses 1 MiB and 64-byte RTPS bodies and checks received bytes.
It executes no simulated DMA device and produces no latency or throughput
estimate. See the
[probe README](../../../profiling/chimaera_rewrite/head_of_line/README.md)
for exact build/run commands and limitations. Production transport code was
unchanged.
