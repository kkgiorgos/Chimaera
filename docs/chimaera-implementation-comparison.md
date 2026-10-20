# Chimaera throughput and latency comparison

Measured on `ubuntu-george`, 2026-10-20, on branch
`feat/chimaera-low-latency-transport`. The completed DMA integration substantially
reduces standalone control latency. At the tested stereo load, M5 and DMA both
deliver all images; DMA provides a small mean image-latency improvement and does
not establish a higher throughput ceiling.

The [interactive comparison](measurements/chimaera-implementation-comparison-2026-10-20/report.html)
includes native-free/native-stepped references, stream delivery rates, latency
statistics and whole-case wall times. The [CSV](measurements/chimaera-implementation-comparison-2026-10-20/comparison.csv)
and [JSON](measurements/chimaera-implementation-comparison-2026-10-20/comparison.json)
contain 147 measurements from 36 cases: 18 historical and 18 final acceptance
cases, including both native modes. Existing completed runs supply the data;
generating this comparison did not run additional simulations.

## What the rates and times mean

Latency is actual source publication to the receiving callback, in
**guest-visible/co-simulation milliseconds**. Age starts at scheduled release
and includes late source scheduling. A stereo pair finishes at the later camera
callback. Native timestamps use 1 ms logical clock buckets; an average below
1 ms is an average of quantized observations. Zero means the same bucket.

Throughput here means unique receipts from a finite source cohort, including
arrivals during its drain, divided by the source-window duration. A delivered
rate of 30 stereo pairs per source-second means all 60 pairs generated in the
2 s source window arrived before drain ended. It does not mean all callbacks
occurred inside those exact 2 s, or establish a maximum sustainable rate.
No saturation sweep has been run. All retained receipts are ordered and unique.

Payload goodput counts application body bytes, excluding serialization,
transport headers and other streams. Stereo RGB uses 921,600 bytes per image:
`2 × 640 × 480 × 3 × 30 = 55,296,000 bytes/s`, or **55.296 decimal MB per
source-second**. Pair rows and per-camera rows describe overlapping bytes;
they must not be added together. The CSV leaves goodput empty for streams whose
body size is not recorded, rather than inventing a size from serialized length.

## Final matched comparison: M5 rewrite versus integrated DMA

The M5 reference includes the streamed protocol, buffer optimizations and
borrowed ROS publication. Both backends use identical endpoint binaries and
the same 16 MiB DDS profile. Detailed CPU settings match deployment: two
TimingSimpleCPU cores at 3 GHz, 16 KiB L1I/L1D, 256 KiB L2, MESI_TWO_LEVEL,
DDR3_1600 and 3 GiB RAM. Synchronization is 2 ms; M5's poll limit is 1 ms.
KVM is excluded from this comparison.

Each case has a 0.25 s warmup and 2 s source window, followed by a 0.5 s control
or 0.75 s stereo drain. Each backend has its own native-free and native-stepped
runs on the same machine, with matching traffic, endpoints and DDS bytes.
The native gap includes modeled guest CPU and memory behavior as well as the
bridge. It is not a measurement of bridge CPU cost alone.

Standalone 64-byte controls target 97 Hz in each direction:

| Backend / path | Received / published | Delivered messages / source-s | Mean / p99 delay, ms | Mean age, ms | Native mean delay, free / stepped, ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| M5, host → guest | 194/194 | 97 | 4.742 / 10.370 | 5.731 | 0.000 / 0.072 |
| DMA, host → guest | 194/194 | 97 | 0.544 / 0.651 | 1.534 | 0.098 / 0.057 |
| M5, guest → host | 194/194 | 97 | 4.099 / 8.019 | 6.144 | 0.459 / 0.052 |
| DMA, guest → host | 194/194 | 97 | 1.060 / 1.096 | 3.093 | 0.062 / 0.046 |

All native controls also receive 194/194 messages. Each backend delivers
6,208 payload bytes per source-second per direction, or 12,416 combined.
DMA reduces observed mean delay by **88.5% into the guest and 74.1% out**.
Delivery throughput is equal at this load. DMA's guest source still releases
messages about 2.032 ms late on average, explaining why age remains higher
than transfer delay.

Stereo uses two 640×480 RGB cameras at 30 Hz, accompanied by 250 Hz joint
feedback and effort commands plus a 1 kHz clock stream:

| Policy / backend | Pairs received / published | Pairs / source-s | RGB MB / source-s | Pair mean / p99 delay, ms | Pair mean / max age, ms | Native pair mean delay, free / stepped, ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Latest, M5 | 60/60 | 30 | 55.296 | 23.351 / 30.330 | 24.018 / 30.996 | 0.800 / 0.250 |
| Latest, DMA | 60/60 | 30 | 55.296 | 22.994 / 29.656 | 23.661 / 30.989 | 0.883 / 0.233 |
| Lossless, M5 | 60/60 | 30 | 55.296 | 26.251 / 36.053 | 26.918 / 36.053 | 0.333 / 0.233 |
| Lossless, DMA | 60/60 | 30 | 55.296 | 24.419 / 38.886 | 25.085 / 38.886 | 0.583 / 0.300 |

Every native run delivers all 60 pairs, also 55.296 RGB MB per source-second.
DMA improves observed mean pair delay by **1.5% with latest delivery** and
**7.0% with lossless delivery**. Its observed lossless maximum age is worse;
both backends exceed the 33.3 ms camera period for some lossless pairs.
One repeat and 60 pairs give limited evidence for rare tails or small differences.

Latest delivery permits replacement/expiry and retains small application sensor
queues. Its image throughput does not describe the accompanying streams:

| Policy / backend | Joints: receipts; delivered / source-s | Efforts: receipts; delivered / source-s | Clock: receipts; delivered / source-s |
| --- | ---: | ---: | ---: |
| Latest, M5 | 490/500; 245 | 240/501; 120 | 230/2000; 115 |
| Latest, DMA | 446/500; 223 | 265/499; 132.5 | 362/2000; 181 |
| Lossless, M5 | 500/500; 250 | 501/501; 250.5 | 2000/2000; 1000 |
| Lossless, DMA | 500/500; 250 | 499/499; 249.5 | 2000/2000; 1000 |

Lower latest-policy survivor latency must be read with those losses. Effort
publication counts vary slightly at the source-window edges. Lossless uses
reliable depth 128 and disables replacement/expiry; it delivers every actual
publication in these cases, rather than promising arbitrary-overload delivery.
Native joints and efforts are all 500/500. Native latest clock counts are
1961/2000 free and 1899/2000 stepped for M5, and 1934/2000 free and 1920/2000
stepped for DMA. Native lossless clocks are all 2000/2000.

## Historical comparison: original and early rewrites

These pilots use the same detailed CPU geometry and 2 ms synchronization, but
only a 0.2 s source window, 0.05 s warmup and 0.15 s drain. They retain older
DDS settings and endpoint versions. They show the earlier milestones;
**do not calculate an original-to-DMA speedup across these two cohorts**.
The optimized buffer rewrite is remeasured as M5 in the final cohort above.

| Implementation | Control mean delay H→G / G→H, ms | Control receipts H→G / G→H | Control delivered / source-s H→G / G→H | Image mean delay left / right, ms | Image receipts left / right | RGB MB / source-s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original | 6.672 / 5.810 | 20/20; 19/19 | 100 / 95 | 46.697 / 59.714 | 6/6; 5/6 | 50.688 |
| Streamed protocol, executor and borrowed publication | 4.907 / 4.656 | 20/20; 20/20 | 100 / 100 | 17.555 / 23.178 | 5/6; 4/6 | 41.472 |
| Receive/source buffer initialization optimized | 5.026 / 3.702 | 20/20; 20/20 | 100 / 100 | 17.221 / 26.719 | 6/6; 6/6 | 55.296 |

The 97 Hz control target produces integer cohorts of 19 or 20 publications,
so normalizing this short window gives 95 or 100 messages per source-second.
Image rates are 30/25 frames per source-second for the original, 25/20 for the
first rewrite and 30/30 for the buffer rewrite. Complete-pair metrics were not
exported in these pilots; counts from separate eyes must not be treated as
proof of matching pair IDs. Lower first-rewrite image latency accompanies
more lost images. Its native-free run also loses one left frame (5/6); all
stepped-native pilot images and the other native-free pilot images arrive.
All native pilot controls receive 20/20 in each direction. The interactive
report and CSV retain each milestone's own native means and counts.

## Wall-clock cost and large images

Whole-case execution records for the final detailed CPU cases:

| Workload | M5 wall seconds | DMA wall seconds |
| --- | ---: | ---: |
| Standalone controls | 985.6 | 1057.3 |
| Latest stereo | 4227.4 | 4811.3 |
| Lossless stereo | 5986.1 | 5971.9 |

These include setup, warmup, drain and export under shared host load and
container quotas. They do not establish an isolated simulator speedup or
wall-clock maximum throughput. The interactive report also retains both native
wall times. Guest-visible delay and simulator execution cost are separate axes.

Separate correctness runs verify full image contents at 640×480 and
1280×960 RGB, with 7.37 MB per large stereo pair, plus large bidirectional
packets and DMA guard/reset behavior. The large full-image case runs at 2 Hz;
it does not establish 30 Hz performance at that resolution. DMA's default
test pool has 8 MiB slots; this is bounded packet transfer, not unlimited-file
streaming. Instrumented and full-byte scanning cases are excluded from these
performance tables because their extra guest work changes timing.

In one joined integrated trace, coherent DMA transfers the 1.84 MB stereo
packet in 0.163 ms while guest bridge ROS publication takes about 21.087 ms
of a 24.785 ms publication-to-last-callback interval. This explains why DMA's
large standalone control improvement does not translate into a similarly large
image improvement. The next image target is the ROS publication path and its
copying, locks and scheduling; the current trace does not attribute individual
DDS subfunctions. See the [full integration record](chimaera-dma-integration.md)
for the trace, correctness evidence, implementation limits and local tests.

## Reproduction

From the repository root:

```sh
python3 profiling/chimaera_rewrite/compare_implementations.py
python3 -m unittest discover -s profiling/chimaera_rewrite -p 'test_compare_implementations.py' -v
```

The exporter revalidates both native modes and matching endpoint/DDS identities,
checks active detailed CPU provenance in acceptance, and checks equal M5/DMA
traffic and CPU settings. Seven checks cover loss-aware normalization, stereo
byte accounting, invalid/duplicate cohorts, missing native/CPU provenance,
changed cross-backend settings and CSV round trips. Source hashes are stored
in the JSON. Raw device-only probes remain separate from ROS latency and
throughput; dividing their payload by a single round-trip time would not
establish end-to-end ROS capacity.
