# DDS m5ops execution and memory-cost diagnostics, 2026-10-23

Application execution isolation removes the observed control stall in the tested transient. Camera/bulk isolation and maximum sustainable throughput are not established.

Two guest cores, 3 GHz, 16 KiB L1I/L1D, 256 KiB L2, 3 GiB RAM. Timing uses active BaseTimingSimpleCPU cores; KVM correctness is separate. Stereo is 480x360 RGB at 30 Hz/eye, joints/effort 250 Hz, clock 1 kHz. Timing source/warmup/drain are 0.2/0.05/0.1 s, with one measured 8 MiB sample/direction in loaded cases.

[Interactive report](report.html), [full evidence](summary.json), [per-stream CSV](metrics.csv).

The primary requirement is actual sample creation/capture to callback entry. This benchmark preallocates and reuses payloads and lacks an actual creation timestamp. Its `latency` is publish-call-entry to callback; `age` additionally includes source scheduling delay. Neither is relabeled as the complete creation-to-callback metric. Both memory-cost modes are retained separately. Functional mode deliberately excludes scoped memory-copy costs, which need calibration/representation for a physical offload target.

## Execution isolation

Both layouts share one DDS participant and unchanged transport pools. Split execution adds one application executor for image/bulk publication and typed takes. The first diagnostic also has a second 1 kHz source timer; its instruction cost is included.

| Control | Merged unloaded p99 range | Merged +8 MiB p99 | Split unloaded p99 range | Split +8 MiB p99 |
|---|---:|---:|---:|---:|
| joint_states | 3.291–5.979 ms | 113.098 ms | 1.177–4.360 ms | 2.429 ms |
| effort_command | 2.000–4.000 ms | 82.000 ms | 2.000–4.000 ms | 4.000 ms |
| clock | 5.290–6.562 ms | 117.123 ms | 2.073–4.402 ms | 3.776 ms |

The merged loaded case directly records about 76 ms inside guest bulk publication and twelve missing scheduled effort releases. Split execution keeps measured control age within the observed unloaded range and emits those effort releases. Cameras still reach about 102–104 ms age; bulk delivery is slower. This is control protection, not full flow isolation.

The nominal final raw instruction dumps are about 64–68 million for merged execution and 71–79 million for split execution. These retain their original scopes (warmup/drain/export may be included); they are not source-window-only costs and are not summed.

## Separate middleware-copy model

The optional helper offloads 64 KiB–32 MiB copies/fills only in marked RMW/receive scopes. Application work, prefault stores, calls and remaining middleware stay modeled. It creates no receive pool or worker thread. Both arms preload the same helper. Real KVM verification checks all Image bytes and model counters; faults or absent/incorrect activity reject an arm.

- copies-ball-guest-kvm-r1: complete
- copies-ball-functional-kvm-r1: complete
- copies-bulk8m-guest-kvm-r1: complete
- copies-bulk8m-functional-kvm-r1: complete
- copies-bulk8m-guest-timing-r1: complete
- copies-bulk8m-functional-timing-r1: complete
- copies-ball-guest-timing-r1: complete
- copies-ball-functional-timing-r1: incomplete; Directory exists without final attempt record

| Mode / load | Left / right camera max | Joint / effort / clock p99 | Guest bulk publish max | Last raw instructions |
|---|---:|---:|---:|---:|
| guest / bulk8m | 63.644 / 67.260 ms | 0.598 / 4.000 / 1.572 ms | 59.037 ms | 103,211,578 |
| functional / bulk8m | 1.582 / 1.640 ms | 0.610 / 2.000 / 1.361 ms | 16.378 ms | 89,084,094 |
| guest / ball | 5.209 / 7.466 ms | 3.089 / 4.000 / 3.779 ms | — | 71,354,113 |

Copy-mode comparisons use their own newly frozen simulator, DDS library and helper in both arms. Cross-phase differences from the execution-only experiment are not attributed to offload. Instruction and memory counters include their retained raw dump scopes; they do not isolate serialization or source-window work.

| Loaded copy mode | Left / right publish-entry → callback max | Host → guest / guest → host bulk max |
|---|---:|---:|
| guest | 63.644 / 67.260 ms | 89.043 / 59.205 ms |
| functional | 0.622 / 0.681 ms | 1.272 / 16.671 ms |

## Actual application and remaining scope

The simulation round stopped after 7174 host seconds within its 7200-second cap, with no owned simulation containers running. The final functional-copy unloaded case was stopped for budget exhaustion; it provides no complete unloaded reference. No further simulations were started.

- actual-robot-kvm-r1: Scene validation failed in the small SDK: missing urdf_parser_plugin metadata
- actual-robot-kvm-r2: [gem5.opt-5] RuntimeError: guest finished before its bridge was ready: m5_fail instruction encountered (code 2); see /work/.chimaera-m5/isolation-20261023/actual-robot-kvm-r2/baseline/gem5/board.pc.com_1.device

Validation: 81 Python tooling checks pass. The native functional-copy contract test passes. Rebuilt DDS checks pass (four executed, one disabled trace test); eight fresh KVM diagnostic cases pass full-payload verification. The two-core actual robot validation remains unsuccessful.

The subsequent creation-timestamp endpoint revision builds and its metric tests pass, but it has not run in a simulator. All retained measurements predate it; historical creation-latency fields remain empty. New host/guest endpoints must be frozen together before qualifying that extended record format.


Per-flow priorities, per-stream lossless/latest policies, flow-specific receive credits and independent camera/bulk service still need implementation/qualification. Idle and sparse bulk-source wakeups still need optimization and qualification; the published comparisons retain the measured 1 kHz timer behavior.

No maximum/sustainable throughput or statistical latency non-regression claim follows from these short windows. Receipt-window rates exclude drain; finite-cohort delivery rates are a different metric. Missing releases, losses, duplicates, ordering, backlog and all incomplete/unstarted cases remain in the JSON/CSV.

Raw runs and frozen identities: `.chimaera-m5/isolation-20261023/`. The two-hour round uses an absolute deadline and does not reset between suites.
