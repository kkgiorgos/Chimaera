# Fast DDS middleware validation, ubuntu-george, 2026-10-21

See the [implementation and measurement report](../../chimaera-dds-middleware.md).

`ball-result.json`, `ball-benchmark.json`, `ball-execution.json`,
`ball-launch-validation.json` and `ball-provenance.json` retain the clean KVM
baseline result, configuration, scoring and artifact identities. `ball-ages.json`
contains ages of received records during the throw, and `ball-wall-phases.json`
aggregates the step timing records. `application-source-check.json` records that
the normal robot application package is unchanged. The base revision plus the
retained source patch identify builds made before their changes were committed.

`runtime-provenance.json` fingerprints the immutable `runtime-queue-r1` prefix
used for the catch and retains the older staging artifact hashes.
`runtime-versions.txt` records SDK versions.
`transport-tests.log`, `queue-tests.log` and `bridge-tests.log` retain CTest
results. `report-tests.log` covers matched control and report analysis checks.

Diagnostic evidence is separate: `ball-clock-diagnostic-*.json`, `clock-probe.json`,
`dma-startup-progress.txt`, and `queue-overflow-attempt.json`. The native baseline
is also retained. Diagnostic and failed attempts are not performance samples.

Raw runs, including every interrupted attempt, remain locally under the ignored
`.chimaera-middleware/` directory. The clean application run is
`ball-acceptance-r5`; the matched benchmark is `latency-acceptance-r3`.
Earlier benchmark directories retain the timeout-parameter and missing-library
startup failures. `bundle-library-preflight.txt` verifies the corrected bundle
without workspace/build paths mounted; `old-bundle-library-preflight.txt`
reproduces the missing dependency. `guest-runtime-libraries.json` and
`latency-runtime-provenance.json` identify the final portable benchmark bundle.
Its 12 compiled runtime artifacts are identical to the successful ball runtime.

All eight matrix cases completed. `delivery-check.json` has no failures.
`attempts.json` retains every final case's full summary, configuration, artifact
hashes, provenance and active CPU classes. `comparison.csv` compares matched
native references; `middleware-comparison.json` and `.csv` compare the bridge
and middleware directly. Negative mean reductions indicate increased delay.
`report.html` provides an interactive view of those data.

The image result is a regression: complete-pair mean delay rises from 19.082
to 20.704 ms (8.5%). Effort-command mean delay falls 55.8%. Both small-message
directions improve in mean, with a worse host-to-guest maximum. All streams
are delivered completely, ordered and unique. These results do not establish
an image-path improvement.

`resource-costs.json` contains whole-guest counters and case wall time, with
hashes for the original statistics; `*-stats.txt.gz` preserves their complete
contents. Instructions increase 8.4% for the stereo workload and 27.2% for small
messages. `latency-base-revision.txt`, `latency-source.patch.gz` and
`latency-source.tar.gz` preserve the measured source snapshot. Final report
tooling was committed after the run started; compiled runtime files remained
frozen. The report records the short window, native clock quantization and
configured-versus-delivered control rate limitations.

Source patches use gzip compression to preserve their exact bytes, including Git
patch context whitespace. `source-patches.json` records uncompressed hashes;
`gzip -dc FILE.patch.gz` recovers a patch. The ball run's base revision is in
`ball-provenance.json`, with its changes in `ball-source.patch.gz`.

The additional SHM-disabled host-only baseline is retained separately:

- `native-attempts.json`: four complete UDP cases, exact configuration and binary hashes.
- `native-comparison.json` / `.csv`: 16 matched stream/pair comparisons against
  the original native SHM controls; no original gem5 comparison is replaced.
- `native-report.html`: interactive native comparison. The main `report.html`
  also includes both new UDP native references in its detail table.
- `native-udp.xml` and `native-udp-process-check.json`: explicit UDP-only profile
  and live process environment/transport-SHM absence check.
- `native-base-revision.txt`, `native-source.patch.gz`, `native-source.tar.gz`:
  exact launcher source snapshot. `native-workflow-tests.log` records 235 passing
  tests and two environment-dependent skips after the change.
- `native-initial-attempt.json`: the excluded 60 s watchdog attempt. Its complete
  rerun uses a 300 s watchdog; raw/interrupted output remains locally in
  `.chimaera-middleware/native-udp-r1`, with final runs in `native-udp-r2`.

The free-running UDP stereo-pair mean is 3.933 ms versus 2.367 ms with SHM.
Joint feedback has a 67.044 ms mean and 231 ms p95 despite complete delivery.
The 1 ms native timestamp buckets, short single-repeat window, UDP fragmentation
and stepped clock pacing limit interpretation; see the main report.
