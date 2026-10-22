# KVM iteration results: direct DDS/m5ops

The actual KVM ball-catching experiment passed (`inside_cup_at_deadline`, `cup_capture`, 0.5315 s containment). All eight matched traffic implementations completed qualification with payload verification and no missing, duplicate or reordered published-cohort receipts.

Each traffic run has one 1-second source window, 0.25-second warmup and 0.75-second drain. The workload is 480×360 RGB stereo at 30 Hz per eye, joints and efforts at 250 Hz and clock at 1 kHz. These runs use two KVM CPUs, not timing CPUs. No supplemental bulk stream was enabled.

All eight delivered **31.104 MB/s of stereo image bodies** in the fixed interior receipt-time interval [0.5 s, 1.0 s), equivalent to 30 frames/s per eye. This is observed throughput at the configured load, not maximum capacity. Every run delivered 30/30 measured source-cohort frames per eye, 250/250 joint samples and 1000/1000 clock samples. Effort source counts range from 246 to 252; every published source-cohort effort sample was received. Counts differ because selection uses actual publication time; scheduled releases and lag remain in the CSV/JSON.

Publication-to-callback p99 latency in milliseconds (one window; camera p99 is its sample maximum):

| Implementation | Left image | Right image | Joints H→G | Effort G→H | Clock H→G |
|---|---:|---:|---:|---:|---:|
| Original m5 | 15.61 | 22.97 | 23.72 | 79.50 | 26.91 |
| Streamed m5 | 19.46 | 21.22 | 22.47 | 17.34 | 29.46 |
| Initialized m5 (READY) | 27.64 | 30.04 | 33.45 | 23.41 | 37.69 |
| Integrated DMA | 18.92 | 25.07 | 29.58 | 21.68 | 37.85 |
| Original DDS/DMA | 24.56 | 46.11 | 77.89 | 15.00 | 83.42 |
| Copy-reduced DDS/DMA | 14.70 | 20.08 | 21.20 | 9.12 | 22.66 |
| Scheduling DDS/DMA | 13.00 | 15.42 | 16.67 | 7.60 | 18.87 |
| New DDS/m5ops | 25.80 | 33.06 | 57.59 | 6.45 | 71.81 |

The new backend reduced effort-command median latency from 3.40 ms to 1.47 ms versus scheduling DDS/DMA, and effort p99 from 7.60 ms to 6.45 ms. Its joint-state p99 rose from 16.67 ms to 57.59 ms and clock p99 from 18.87 ms to 71.81 ms. Camera tails also rose, despite lower median image latency. This window therefore does **not** establish latency non-regression; the host-to-guest stalls need investigation. One KVM window does not establish that the difference will reproduce or predict timing-CPU tails.

The later unprefaulted initialized-buffer FINAL artifact is retained separately as a diagnostic with two KVM `writeBlob` failures. The table uses the historically benchmarked prefaulting READY artifact. READY binary pins are established, but exact binary/source correspondence remains unverified.

The long timing pipeline was stopped at the user’s request. One completed new-backend timing run and the interrupted scheduling-DMA run remain preserved separately. Neither is mixed into this KVM table. Any timing restart requires explicit user approval.

Raw qualification index: `.chimaera-m5/kvm-qualification-r3.json`. The accompanying [JSON](kvm-results.json) records source reports, input hashes, CPU evidence and raw-file hashes; [CSV](kvm-results.csv) includes sample counts, all latency summaries, age, source lag and receipt-time rates.
