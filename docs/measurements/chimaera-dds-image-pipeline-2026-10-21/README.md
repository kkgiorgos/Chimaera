# KVM DDS image pipeline evidence

See [the detailed report](../../chimaera-dds-image-pipeline-profile-2026-10-21.md) and [interactive attribution](report.html).

- `analysis.json`: image IDs, original publish/callback timestamps, complete additive partitions, auxiliary work spans, rejected joins, device matching errors, and per-camera summaries. Rejected images remain in the endpoint latency statistics.
- `comparison.csv`: every successful native/KVM control and experiment. `traced`, core count, interval, quantum, verification, delivery count and wall duration distinguish configurations.
- `raw-evidence.tar.gz`: exact case lists, commands, run/summary/event records, profile provenance, per-process traces, DMA traces, clock origins, build logs, failure records and the export script. Extract into a temporary directory; all paths are relative.
- `evidence-manifest.json`: SHA-256 for each file in that archive.
- `profile-source.tar.gz`: the copied source used for the traced runtime, including pinned RMW hooks and diagnostic code.
- `profile-source-gated.tar.gz`: the same source with the deadline-clock read gated when recording is disabled. The corrected `gated-off` controls use this variant; `overlay-off` retains the earlier deadline read and is not a clean instrumentation-off reference.
- `attempt-history.json`: all attempted cases, including preflight failures and the cancelled incomplete diagnostic.
- `provenance.json`: base revision, physical host, SDK identity and profiler/source hashes.

All new guest simulations use KVM. Native controls run without a VM. The older TimingSimpleCPU result discussed in the report is contextual evidence only; it was not rerun or decomposed by these KVM measurements.

For a retained traced case directory, regenerate attribution with:

```sh
python3 profiling/chimaera_rewrite/analyze_pipeline.py CASES_ROOT --output analysis.json
python3 profiling/chimaera_rewrite/render_pipeline.py analysis.json report.html
```

The standalone renderer also accepts the consolidated `analysis.json` here. The raw archive's `export.py` records the consolidation procedure for the original workspace layout.
