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

## Integrated ROS DMA validation

The latency runtime now advertises driver ABI 1 and controller protocol 2.
Rebuild and restage it; protocol 1 prototype bundles are rejected before boot.
Run an exported experiment with `transport_backend: "dma"`,
`single_boot: false`, and `dma_module` pointing to the exact guest-kernel module.
The module is injected into the ephemeral guest image at boot. The deployed CPU
configuration and stereo shape remain unchanged.

`delivery_policy: "latest"` retains application sensor QoS and atomic stereo
replacement. `delivery_policy: "lossless"` uses reliable depth-128 endpoints,
keeps every complete stereo pair, and disables age expiry. All comparisons must
match that policy in native-free and native-stepped execution. Always report
source counts, receipts, duplicates, ordering, source release lag and frame age.
`verify_payload: true` checks every image byte with changing capture markers;
these correctness runs add guest CPU work and are separate from performance runs.

The case runner accepts `--append` for distinctly named attempts and
`--source-snapshot` for the retained build archive. It never overwrites a case.
A case's `image_diagnostics: true` enables existing bridge publication-stage
traces. `config.gem5_debug_flags: "ChimaeraDma"` enables descriptor, payload and
IRQ traces in `gem5/dma-debug.log`. `config.dma_diagnostics: true` additionally
exports the last guest bridge phase and buffer counters; it adds guest m5 calls
and should be disabled for performance acceptance.

Use `dds_shm_bytes: 16777216` for the local stereo workload in **all three**
execution modes. A 921,600-byte image exceeds Fast DDS's default 512 KiB segment.
The benchmark generates and fingerprints an explicit loopback UDP/SHM profile,
sets `ROS_LOCALHOST_ONLY=0` to prevent Humble from appending a second default SHM
transport, and injects the same profile into the guest. Leave enough `/dev/shm`
for each participant's segment (three native participants use about 49 MiB at
this setting). Zero retains middleware defaults for historical reproduction.

Export the complete integration history, including startup failures and
cancelled attempts, with:

```bash
python3 profiling/chimaera_rewrite/collect_integrated.py \
  /path/to/integration-r5-m5-acceptance /path/to/integration-r5-dma-acceptance \
  --physical-host ubuntu-george --output /tmp/integrated-results
```

Completed measurements require both native modes with matching traffic,
endpoint/clock/DDS fingerprints, middleware and container identity. Physical
host identity is supplied explicitly because the recorded per-process hostname
identifies its isolated container. Detailed CPU provenance is checked before
export; incomplete attempts remain in JSON and never enter comparison CSV.
Use `--history-only` to archive older diagnostics without claiming that they
have matched acceptance references. `render_integrated.py RESULTS/attempts.json
report.html` produces the standalone interactive acceptance report.

`profile_integrated.py CASE breakdown.json` separates descriptor-header-to-
payload setup, coherent payload DMA, bridge view setup, ROS publication calls
and the later callback. Its JSON retains individual samples and limitations.
Descriptor fetch and earlier queues precede `HEADER`; TX setup can include
mailbox backpressure. Throttled bridge traces and DMA records lack a common
application ID. The parser permits a FIFO inference only when every source
capture has exactly one ordered receipt per camera and the count/size of bulk
RX transfers agrees. It then checks that the intervals reconstruct callback
latency; otherwise it reports separate intervals and the reason it refused
attribution. Completion-to-ACK includes IRQ delivery and driver handling.

Serial-console tracing can add modeled delay. `make_tmpfs_trace_bundle.py
BASE_BUNDLE M5_UTILITY NEW_BUNDLE` keeps runtime binaries unchanged, redirects
guest stderr into RAM, and exports the file during drain with address-mode m5.
Use it only for diagnostics: the source window must end before 0.75 s and drain
must continue past 0.9 s. The retained RAM-profile matrix uses 0.35 s warmup,
0.2 s source and 1 s drain. Exported `chimaera-image-trace.log` is read alongside
device traces. Native controls use the same endpoint/clock/DDS binaries.

The first timer-based RAM attempt completed its ROS measurements but did not
export a file; retain it without attributing ROS stages. Prefer the corrected
exporter, which waits for the origin file with inotify, checks gem5 elapsed time,
and exports at 0.7 s. Its 1 ms Linux sleeps only pace rechecks; they do not define
the export deadline. It also exports guest kernel and middleware versions.
This helper adds modeled work and remains diagnostic-only:

```bash
c++ -std=c++20 -O2 -no-pie -Igem5/include -Igem5/util/m5/src \
  profiling/chimaera_rewrite/trace_exporter.cpp \
  gem5/util/m5/build/x86/out/libm5.a -o /tmp/trace-exporter
python3 profiling/chimaera_rewrite/make_tmpfs_trace_bundle.py \
  BASE_BUNDLE gem5/util/m5/build/x86/out/m5 NEW_BUNDLE \
  --export-helper /tmp/trace-exporter
```

`dds_large_messages.py` is a separate profiling adapter. It interprets
`dds_tuning: "large_shm_only"` and `dds_max_message_bytes: 4194304`, generating
a SHM-only 16 MiB segment with a 4 MiB message limit. Keeping a default UDP
transport also retains its smaller global message limit in Fast DDS 2.6.
This local-participant experiment removes that constraint; it cannot serve
remote DDS participants. Runtime, guest and simulator binaries stay unchanged.
The ordinary case runner does **not** interpret these two experiment fields.
Use the adapter explicitly and compare against native controls with its same XML:

```bash
python3 profiling/chimaera_rewrite/dds_large_messages.py \
  profiling/chimaera_latency/run_cases.py EXPERIMENT_CASES OUTPUT \
  --runtime RUNTIME --bundle BUNDLE --gem5-root SDK \
  --source-snapshot SOURCE_ARCHIVE
```

Each case retains `dds-overlay.json` with the adapter hash and settings, and
the exporter requires matching tuning/backend fields as well as DDS bytes.
`dds_profile_probe.cpp` independently checks the middleware's parsed default
participant settings without creating a participant or consuming a socket:

```bash
c++ -std=c++17 -O2 -I/opt/ros/humble/include/fastrtps \
  -I/opt/ros/humble/include/fastcdr profiling/chimaera_rewrite/dds_profile_probe.cpp \
  -L/opt/ros/humble/lib -lfastrtps -lfastcdr -o /tmp/dds-profile-probe
LD_LIBRARY_PATH=/opt/ros/humble/lib /tmp/dds-profile-probe OUTPUT/CASE/dds.xml
```

The probe exits successfully only for the experiment's single SHM transport,
disabled builtins, 4 MiB message limit and 16 MiB segment. Run it against the
actual benchmark middleware installation and retain its output.
