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

## DMA device validation

Build gem5 with the new PCI device, and build the driver against the exact guest
kernel headers as described in `gem5-transport/kernel/README.md`. The deployed
guest is `5.15.0-142-generic`. Compile the standalone probes from the repo root:

```bash
c++ -std=c++20 -O2 -no-pie -Igem5/include -Igem5/util/m5/src \
  profiling/chimaera_rewrite/dma_probe.cpp gem5/util/m5/build/x86/out/libm5.a \
  -o /tmp/chimaera-dma-probe
c++ -std=c++20 -O2 -Igem5/include \
  profiling/chimaera_rewrite/dma_host_echo.cpp -o /tmp/chimaera-dma-host-echo
```

Run in an isolated socket namespace with read-only image/kernel mounts. Runtime
points to the installed `chimaera_ros_bridge` prefix containing the timing config.
The runner always performs its native reference on the same machine first:

```bash
python3 profiling/chimaera_rewrite/run_dma_probe.py /tmp/dma-result \
  --gem5 /path/to/new/gem5.opt \
  --config profiling/chimaera_rewrite/gem5_dma_probe.py \
  --runtime /path/to/installed/runtime --image /assets/guest.img \
  --kernel /assets/kernel --module /path/to/chimaera_dma.ko \
  --probe /tmp/chimaera-dma-probe --cpu-type timing --dma-window 64
```

Add `--echo-helper /tmp/chimaera-dma-host-echo` to traverse real host mailboxes
instead of device loopback. Only paused host boundaries publish echoed input;
the runner calls `CHIMAERA_INGRESS` before each step. `--step-us` defaults to 2000.
`--cpu-type kvm` is available for smoke checks. The probe loads the module itself,
uses one 8 MiB RX and TX buffer, and verifies 0 B, 63 B, 64 B, 4096 B, 4097 B,
921,600 B, 1,843,200 B, 1,843,337 B and 7,372,800 B transfers twice each. Dirty
guard bytes beyond every payload check partial-cache-line preservation. It also
tests invalid requests, exclusive open, reset with DMA in flight and reopen.
The module's production pool defaults remain four RX/eight TX buffers.

These device microbenchmarks do not contain ROS serialization or callback work.
Do not substitute them for the native/Chimaera ROS comparison matrix. Separate
loopback and host-boundary results; only the latter include synchronization wait.
Each attempt retains a native result, exact command, config/runner snapshots,
CPU origin metadata, binary/module hashes, byte-verified samples and its outcome.
Failed attempts remain visible. `--debug-flags RubyDma,ProtocolTrace` diagnoses
coherence failures but generates very large logs; avoid it for routine runs.
`collect_dma.py ROOT OUTPUT.json` retains all attempts and verifies matched
native payloads, successful integrity/reset results and detailed CPU provenance.

Generate the interactive pilot comparison from the retained CSV with:

```bash
python3 profiling/chimaera_rewrite/render_report.py \
  docs/measurements/chimaera-rewrite-2026-10-20/comparison.csv /tmp/pilot.html
```
