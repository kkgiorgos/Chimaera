# Implementation comparison, ubuntu-george, 2026-10-20

See the [comparison and interpretation](../../chimaera-implementation-comparison.md).

- `report.html`: standalone interactive comparison, including both native modes,
  all streams, source-cohort throughput, latency and whole-case wall times.
- `comparison.csv`: 147 stream/pair rows from 36 measured cases, including native.
- `comparison.json`: the same rows plus definitions, cohort boundaries and source
  paths/hashes. Stereo pair and per-camera rows overlap; do not sum their bytes.

These are derived from the committed historical rewrite `runs.json` and final
DMA acceptance `attempts.json`. No new simulation was needed. The historical
0.2 s legacy-DDS cohort and final 2 s matched-DDS cohort remain separate.
Throughput counts receipts from a source cohort including drain, divided by its
source duration; this is a fixed-load delivery result, not maximum capacity.

Regenerate from the repository root with:

```sh
python3 profiling/chimaera_rewrite/compare_implementations.py
```

Validation: all 16 rewrite-analysis unit checks pass, including seven new
comparison checks. Browser preview exercised all 48 selector combinations
without console errors and checked desktop/light and mobile/dark rendering.
