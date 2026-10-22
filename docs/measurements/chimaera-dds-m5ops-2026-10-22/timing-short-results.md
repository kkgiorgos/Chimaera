# Short TimingSimpleCPU comparison

**Outcome:** all eight ball-like baselines completed; eleven of twelve total cases completed. The new backend improved baseline latency versus scheduling DDS/DMA, but the no-added-control-latency objective remains unmet under 8 MiB traffic. Historical scheduling DMA failed that bulk case with a controller queue limit, so there is no numeric bulk speedup comparison.

This is the user-approved replacement for the cancelled long suite. It contains eight ball-like implementation runs, two focused bidirectional 8MiB payload runs, and two shared native-free SHM references. No stepped native or UDP reference ran. KVM iteration results are [reported separately](kvm-results.md).

Each case uses a 0.2s source window, 0.05s warmup and 0.1s drain. The workload is 480×360 RGB stereo at 30 Hz, joints/efforts at 250 Hz and clock at 1 kHz. Hardware is two TimingSimpleCPUs at the established 3GHz setting, 16KiB 8-way L1I/L1D, 256KiB 16-way L2, MESI_TWO_LEVEL and 3 GiB DDR3_1600. Coupling is 2 ms and physics is 1 ms. All cases use the same 64 MiB SHM/large-message DDS profile and reliable/lossless policy. Full payload checking is disabled for these performance runs; KVM qualification with byte verification preceded them.

Throughput below is stereo body bytes received in the fixed callback-time interval [0.05s,0.25s), decimal MB/s, excluding drain. Latency is application publication to callback in simulation-clock milliseconds. These are short transient observations, not sustainable capacity or a confidence-based tail claim.

| Implementation | Stereo receipt-window MB/s | Joints median / max ms | Effort median / max ms | Clock median / max ms |
|---|---:|---:|---:|---:|
| Original m5 | 33.70 | 9.96 / 25.15 | 88.53 / 115.07 | 11.07 / 25.61 |
| Streamed m5 | 31.10 | 4.73 / 18.52 | 4.09 / 17.82 | 5.71 / 19.41 |
| Initialized m5 (READY) | 31.10 | 4.74 / 18.38 | 3.60 / 13.61 | 5.24 / 19.15 |
| Integrated DMA | 31.10 | 1.44 / 17.03 | 1.65 / 13.96 | 2.80 / 17.51 |
| Original DDS/DMA | 36.29 | 3.80 / 24.76 | 1.79 / 11.88 | 3.95 / 26.94 |
| Copy-reduced DDS/DMA | 31.10 | 0.53 / 11.30 | 1.12 / 3.21 | 0.62 / 11.33 |
| Scheduling DDS/DMA | 31.10 | 0.42 / 9.46 | 1.40 / 5.82 | 0.47 / 9.73 |
| New DDS/m5ops | 31.10 | 0.18 / 7.04 | 1.29 / 1.30 | 0.26 / 7.40 |

Every baseline delivered its twelve measured stereo source-cohort frames (31.104 MB/s when normalized by the source duration, including any drain receipts). Higher receipt-window values for older implementations reflect delayed warmup frames entering the window and do not establish a throughput advantage. This offered load does not measure maximum capacity.

Against scheduling DDS/DMA, median latency changed from 0.42 to 0.18 ms for joints, 0.47 to 0.26 ms for clocks, and 1.40 to 1.29 ms for efforts. The new backend recorded the lowest observed maximum for each of the five streams in these baseline windows. This is descriptive: copy-reduced DDS/DMA still had a lower effort median (1.12 ms), and one window does not establish repeatable tail non-regression.

Source and delivery counts are essential with these small windows. Each cell below is **received / published**, followed by **missed scheduled releases**. Published cohorts are selected by actual publication time; delayed warmup sends can enter them. Absent releases are not counted as network loss.

| Implementation | Left image | Right image | Joints | Effort | Clock |
|---|---:|---:|---:|---:|---:|
| Original m5 | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |
| Streamed m5 | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |
| Initialized m5 (READY) | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |
| Integrated DMA | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 1 | 200/200; 1 |
| Original DDS/DMA | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |
| Copy-reduced DDS/DMA | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |
| Scheduling DDS/DMA | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |
| New DDS/m5ops | 6/6; 0 | 6/6; 0 | 50/50; 0 | 50/50; 0 | 200/200; 1 |

## Focused bulk transfer

Each bulk case adds one scheduled8MiB sample per direction inside the measured source window, plus a sample during warmup. It is a transient overlap test, rather than the cancelled four-image burst test. Body rates exclude framing. Source-cohort receipts may arrive during drain; receipt-time rates do not include them.

| Implementation | Direction | Cohort received / published | Receipt-window samples | Receipt-window MB/s | Latency median / max ms |
|---|---|---:|---:|---:|---:|
| Scheduling DDS/DMA | both | failed | — | — | controller queue limit |
| New DDS/m5ops | h2g | 1/1 | 1 | 41.94 | 119.00 / 119.00 |
| New DDS/m5ops | g2h | 1/1 | 1 | 41.94 | 81.73 / 81.73 |

For the new backend, the measured H→G body arrived at 319.00 ms and the G→H body at 282.00 ms, both during drain. The receipt-window body rates above therefore describe the earlier warmup payloads, not those measured-cohort bodies. Both measured bodies were delivered.

A receipt window can include a delayed warmup payload as well as the measured source-cohort payload. A larger receipt-window rate in this transient test can therefore reflect warmup spillover rather than a faster implementation. Read the receipt count, cohort counts and latency together; do not rank sustained bulk bandwidth from this window.

The following control contrasts use each implementation’s ball-only case as its unloaded baseline, with exactly matched duration, DDS profile, camera rates and CPU geometry. Different publication counts and missing samples can censor latency; they must not be interpreted as an improvement.

| Implementation | Stream | Ball-only median / max ms | With bulk median / max ms | Ball-only vs bulk received / published |
|---|---|---:|---:|---:|
| New DDS/m5ops | joint_states | 0.18 / 7.04 | 41.43 / 123.38 | 50/50 → 50/50 |
| New DDS/m5ops | effort_command | 1.29 / 1.30 | 1.08 / 1.98 | 50/50 → 50/50 |
| New DDS/m5ops | clock | 0.26 / 7.40 | 48.62 / 127.41 | 200/200 → 200/200 |

## Remaining blocking stages

The transport has independent control/bulk readers, send locks and credits, but the benchmark application uses one SingleThreadedExecutor for merged source publication and typed subscription takes. A large publish can block other source releases; typed deserialization occurs before the callback timestamp. Raw events show bulk-adjacent guest source gaps of roughly 72–83 ms and receive callback gaps around 33–34 ms before bulk callbacks. Fast DDS also has a participant-wide send critical section above the transport lanes. These are source-established coupling opportunities, not a measured attribution of every millisecond.

The next bounded diagnostic should separate control and image/bulk application execution and DDS participants on both sides, while preserving source schedules and recording per-stage entry/return times. Ordinary camera images also need consideration. Merely changing to a multithreaded executor with the same default mutually exclusive callback group is insufficient. No such application change or additional benchmark is included in these results.

Static evidence: [bulk-stall review](bulk-stalls-review.md) and [historical queue review](dma-queue-review.md). The former independently recomputed the direct cases’ raw hashes and quoted latency summaries. There is no per-stage profiling trace proving the time split. Performance cases disable full payload verification, including the 8 MiB cases; the earlier KVM byte-verification workload used the nominal ball-like streams.

## Evidence and interpretation

**Source lag also matters.** Under bulk, twelve scheduled effort releases were absent from the source window, although fifty actually published effort-cohort samples arrived. Delayed warmup sends can enter that cohort. Effort publication-to-callback latency stayed near 1 ms while maximum schedule-to-callback age rose from 4 to 80 ms and maximum release lag reached 78.47 ms. Joint and clock age maxima reached 123.38 and 127.41 ms. Low publication-to-callback latency alone therefore does not establish control timeliness.

**The historical bulk failure is a resource-limit result.** The retained DMA controller has a shared 32 MiB outgoing cap and a separate 32 MiB incoming cap. Its outgoing queue is the strongest source-based explanation for the failure, but no C++ stack or queue occupancy was recorded. There is no runtime knob for those controller byte caps. The new host controller instead permits 128 MiB outgoing and 640 MiB incoming per independent lane. The historical DDS transport also advertises a smaller packet limit. Queue capacity, packetization and admission differ; successful new bulk delivery cannot be attributed solely to m5ops or converted into a numeric DMA-to-m5ops speedup.

Each completed guest attempt is checked against the retained CPU-origin list, cache/DRAM configuration, binary identities and raw-file hashes. Dormant KVM boot CPUs do not establish the active timing model. Shared native-free references are accepted only when their declared case, settings, DDS XML and common endpoint/clock/RMW hashes match. Native timestamps have 1 ms simulation-clock quantization; zero is not zero wall latency.

The m5ops backend transfers payload through functional/coherent access without timed DMA payload memory requests. Host copies and guest DDS/instruction work remain; this is a changed payload memory-cost model, not a claim of physically free transfer. The unprefaulted FINAL initialized-buffer pair remains a separate failed KVM diagnostic; the comparison uses the historically benchmarked READY binary. Exact READY binary/source correspondence is unverified.

One short window per cell cannot establish maximum or sustainable throughput, repeatability, or tail non-regression. Camera percentiles have only a few samples; the table therefore emphasizes medians and observed maxima. The JSON/CSV also retain age, release lag, deadlines, losses, duplicates, reordering and backlog. Guest statistics retain their original dump scope, which may include warmup, drain and export; overlapping dumps are not summed.

Selection lock SHA256: `18ac35d7d9cd936b6a4dfc1d87d88f387f3c25be23ecae08b77861a623866f13`.
Matrix SHA256: `0fabc1c750da792169a3f9dc188037e2e014422fd7678bb5e0de240d39af66d5`.

Artifacts: [summary JSON](timing-short-results.json), [per-stream metrics CSV](timing-short-metrics.csv), [matched comparisons CSV](timing-short-comparisons.csv). Raw suite: `.chimaera-m5/timing-short-r3/`; validated full report: `.chimaera-m5/timing-short-r3-report/comparison.json`; tooling source: `.chimaera-m5/tooling-source-r3.tar.gz`.

Incomplete or invalid attempts remain visible:

- short-bulk8m-scheduled_dds_dma-chimaera-shm-r1: failed — controller queue limit exceeded; see driver.log
