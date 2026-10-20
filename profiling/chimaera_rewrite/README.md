# Rewrite comparison and clock experiments

Run native-free, native-stepped and Chimaera cases with identical traffic,
physics, windows, QoS and synchronization settings for **each runtime**. Use the
fresh-boot case runner in `../chimaera_latency/run_cases.py`; `original` selects
its original runtime/bundle arguments and `off` selects the candidate arguments
without instrumentation. Keep separate source/runtime/bundle snapshots for
each iteration. Run one session per socket namespace; place package tests in a
different container from a running benchmark.

Use the deployed two-core TimingSimpleCPU configuration for acceptance. KVM
cases are smoke checks. Every image run should report per-camera source and
receipt counts alongside latency. A short run or latency among survivors alone
cannot establish sustained image throughput.

The retained pilot matrix is in the ignored `.chimaera-rewrite/20261020/raw`
tree. Export it with:

```bash
python3 profiling/chimaera_rewrite/summarize.py \
  .chimaera-rewrite/20261020/raw /tmp/chimaera-rewrite-comparison
python3 -m unittest discover -s profiling/chimaera_rewrite -v
```

The exporter requires both native modes with matching traffic and host identity
for every Chimaera measurement. It rejects zero-publication streams and checks
the active CPU classes/core count in gem5's origin metadata. JSON retains the
full run configuration, provenance and artifact hashes; CSV includes native
references for every stream. Empty receive cohorts remain visible.

`clock_probe.py` is a separate **experimental** guest oracle. It loads instruction
m5 operations only after the detailed CPU switch and origin publication, then
measures Linux time and requested sleeps against gem5 time on each guest core.
It exports `chimaera-clock-probe.json` into gem5's output. Allow sufficient warmup
and check `finished_ns` precedes the source window before interpreting latency.
Never execute its m5 reads under KVM. `run_clock_experiment.py` is a container
wrapper for the private runtime copies retained from this investigation; it
does not alter the repository's production boot or clock settings.
