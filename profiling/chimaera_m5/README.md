# DDS + functional m5ops benchmark preparation

The current first comparison is the **short** stage, approved after the KVM
results were shown. The earlier 110-case timing pipeline was cancelled; its
one completed case and interrupted case remain separate evidence.

```sh
python3 profiling/chimaera_m5/matrix.py --stage short --output NEW_MATRIX.json
```

This fixed stage has twelve cases: eight ball-like guest implementations, two
single-8-MiB bidirectional bulk cases (new m5ops and scheduling DMA), and two
explicitly shared native-free SHM references. It runs no stepped native cases,
UDP references, latest-policy duplicates or repeated windows. Each case uses
0.05 s warmup, 0.2 s source window and 0.1 s drain. All use the same 64 MiB SHM
and large-message DDS profile, preserving the ball-only cases as matched bulk
baselines. Source rates, stereo geometry, CPU/cache geometry and coupling are
unchanged. The burst is one sample per direction per measured window, plus
warmup traffic; this is a transient screen rather than the older four-image
burst test.

Short-stage metrics use fixed receipt windows without trimming or aligning to
whole bulk periods. Shared free-native references must match the declared case,
configuration, DDS XML and common endpoint/clock/RMW hashes. There are about
six camera frames per eye and fifty joint/effort samples per source window.
Report counts, missing releases/receipts, age and descriptive latency; these
windows cannot establish sustainable capacity or confidence-based tail bounds.
Freeze a new tooling lock before execution; do not reuse the old lock after
source changes. The immutable r2 tooling archive and old matched assets remain
retained.

**The first actual KVM ball-catching gate passed at r11.** The parent announced
`success:true` / `cup_capture`, with 0.5315 s containment, and retained its
attestation in `.chimaera-m5/first-catch-pass.json`. Parent regression results
include **34/34 benchmark tooling tests passed**; the same 34 tests passed again
in the pinned SDK after fixes for common guest loader libraries and missing
application dependencies. Freeze now checks the loader copies as well as app/lib.
Staging, asset inspection, freezing and focused tooling tests are authorized;
the parent coordinates all simulations, native benchmarks and KVM/timing runs.

This directory owns a benchmark-only fork of `benchmark/endpoint.cpp` at
912ae01, its shared clock, a standalone build, source/runtime selection tools,
case generation, a serial runner and a raw-event aggregator. Production endpoint,
bridge, runtime configuration, custom DDS and gem5 core are untouched by this
sub-agent. The parent owns the `m5ops`/`fastdds` runtime integration and the
dedicated `m5_module` field. New notification mode always needs a fresh guest.

The extended endpoint compiled and linked successfully with GNU11.4.0 in
`chimaera:middleware-gem5`, network disabled,1CPU and `-j1`, with all three
requested ROS/RMW/runtime setups sourced. Output:
`.chimaera-m5/build-benchmark/latency_endpoint`, SHA256
`c7098ff6074f75be5bcf3bf380febf39b51a3c6c71f200185f57a7fdda7400a2`.
The shared-clock header was compiled into it; pinned clock ABI shared libraries
are supplied by the parent runtime. No executable/test, staging tool, case
generator, benchmark or simulator was run by this sub-agent.

`../../.chimaera-m5/benchmark-plan.md` records the staged budget and located
assets. There are **nine identities**. The eight main comparisons are original
bridge/m5, streamed m5, **initialized_m5_ready**, integrated bridge/DMA, original
custom DDS/DMA, copy-reduced DDS/DMA, scheduling DDS/DMA at912ae01, and direct
DDS/m5ops. The unchanged `initialized_m5` final pair remains a ninth, diagnostic
identity. `matrix.py --stage all-prior` defaults to the eight main identities;
the final pair can be selected explicitly and is excluded from performance
comparisons. Preserve both final-pair raw crashes and the original selection lock.

READY is guest `fd32c7e1…`, driver `a507655c…`, bundle `ea049668…`, as selected by
the historical timing and KVM Ball records. Both app and executed guest-bridge
copies are pinned. The final pair uses guest `cb0671f5…`, bundle `cf113e53…` and
archived source `cd2d87c6…`; its pins are unchanged. Neither historical v2 source
archive contains the receive-buffer prefault helper. READY therefore pins
`.chimaera-m5/initialized-m5-ready/source-provenance.json` and the separately
hashed `retained-source-v2.tar.gz` snapshot from the unpacked source directory.
**Exact binary/source correspondence is UNVERIFIED.** The snapshot does not
claim to be the recovered READY build tree. Parent current-contract runtime
qualification remains necessary.

The catalog accepts the exact previous catalog digest only for its three
existing labels, and requires that the embedded selected entry is unchanged.
READY requires the current catalog. This permits re-freezing untouched existing
legacy bundles without weakening any final or READY executable pins.

## Fresh-container serial execution

After the parent releases the tooling tree and creates a new lock, use the
repository-owned wrapper instead of requiring ignored operator scripts:

```sh
python3 profiling/chimaera_m5/run_isolated.py .chimaera-m5/MATRIX.json \
  --lock .chimaera-m5/selection-2.lock.json \
  --gate .chimaera-m5/first-catch-pass.json \
  --output .chimaera-m5/NEW_SUITE --cases CASE_NAME
```

The host wrapper derives the checkout root from its location (`--root` can
override it), reads the immutable SDK image from the lock, and mounts the
checkout at `/work`, matching the frozen container paths. CLI matrix, lock,
gate and output paths refer to the host checkout. Runtime setups default to
the pinned ROS Humble, `.chimaera-middleware/runtime-staging-r2` RMW overlay and
`.chimaera-m5/runtime`; `--middleware-setup` and `--runtime-setup` accept explicit
host paths when needed. Record these choices with the suite's environment.

`container_case.sh` invokes the frozen `run.py` once per selected case, adding
`--append` only when the suite manifest exists. Each case gets a fresh Docker
container with private `/tmp`, network disabled, `/dev/kvm`, 4 CPUs, 24 GiB RAM
and 2 GiB shared memory. Default CPU affinity is retained. These are parent
runtime settings; focused preparation tests can use fewer resources.

Operator logs live in a new `SUITE-operator/UUID` directory for every invocation.
Existing failed, cancelled, timed-out, invalid or incomplete attempts remain
failures on resume; retry into a new suite. A complete attempt also needs the
container shim's normal-return marker, allowing `run.py`'s expected partial-suite
exit 1 while rejecting an uncaught integrity error. Timeout or cancellation
stops only the UUID-named container whose ownership label matches this invocation.
No simulation was run to test these wrappers: Docker and runner failure paths
are mocked, with a small synthetic runner used to test the return marker.

## Build and immutable staging

Build the extended endpoint in the pinned ROS/RMW SDK. These commands build
only; they do not run CTest or start a container/simulator:

```sh
cmake -S profiling/chimaera_m5 -B .chimaera-m5/build-benchmark \
  -DCMAKE_BUILD_TYPE=Release
cmake --build .chimaera-m5/build-benchmark -j2
```

The endpoint retains stereo480x360 RGB30Hz, joints/efforts250Hz and clock1kHz.
Additional bidirectional bulk uses **sensor_msgs/msg/Image**, named
`bulk_image_h2g`/`bulk_image_g2h`; its encoding is8UC1 and its byte body is
exactly the offered size. This selects the Image-type bulk lane in both
directions without guessing from size. Fixed-width frame_id carries sequence
identity; capture timestamps identify release time even when a burst releases
several distinct sequences together. The separately enabled verifier scans all
image bytes after recording callback entry. Performance disables that scan.

Source schedules remain independent of the receiver. Normal bulk burst1 and
burst4 group the same mean offered rate into bursts. Source release lag and
missed scheduled releases expose a blocked publisher's inability to achieve
that rate. Controls are never increased along with the supplemental bulk.
Control-only turns camera endpoints off while retaining250Hz/1kHz controls.
This endpoint uses one participant/executor per side; it does **not** implement
the additional two-participant isolation, stalled-reader, or internal lane-credit
experiments. Those remain explicit future diagnostics, not hidden coverage.

Stage identical application endpoint, clock ABI and compatible RMW bytes into
**new copies** of each runtime and guest bundle. Preserve each implementation's
`latency_driver`, `guest_bridge`, bridge/controller/DDS libraries and protocol.
The driver statically embeds host transport and publication behavior; sharing a
new driver would invalidate the comparison. Its per-variant identity is frozen
separately from common application assets. For example, after selecting the
common pinned runtime:

```sh
python3 profiling/chimaera_m5/stage.py \
  --runtime .chimaera-middleware/scheduling-egress-20261022/runtime-candidate \
  --bundle .chimaera-middleware/scheduling-egress-20261022/guest-candidate.tar.gz \
  --common-runtime COMMON_PINNED_RUNTIME \
  --endpoint .chimaera-m5/build-benchmark/latency_endpoint \
  --output .chimaera-m5/matched/scheduled_dds_dma
```

Repeat for the selected identities in `selections.example.json`; use the parent's
fresh candidate runtime/bundle for `direct_dds_m5ops`. Staging copies bytes only
and records original hashes. It does **not** upgrade bridge protocols or qualify
legacy capability metadata. Additional linked libraries must be present in each
staged runtime/bundle, without replacing the selected transport implementation.

All four legacy pairs have separate entries in `legacy-identities.json` and
explicit selections in `selections.example.json`. `--legacy-implementation`
accepts `original_m5`, `streamed_m5`, `initialized_m5`, or `initialized_m5_ready`; `--legacy-original`
remains an original-only compatibility alias. The original-only default of the
identity validator cannot admit either rewrite. `legacy-selection.md` records
the asset audit, source/SDK limits and initialized ready/final mismatch.

Each adapter preserves its selected host driver, app guest bridge, **actual
executed** `runtime/guest_bridge`, `guest_start`, and original `latency-runtime.json`
byte-for-byte. It adds current declarative manifest parsing and a narrow Python
runner shim passing only parameters supported by the reviewed old runners.
Endpoint feature metadata describes the replacement application endpoint;
legacy transport metadata remains unchanged, without claiming protocol3.
The old strict route schemas require `coalesce_group` to imply `coalesce:true`;
the legacy route adapter removes inactive group metadata for lossless images,
preserves latest stereo grouping, and rejects unsupported explicit lane metadata.

The retained source archive/provenance, raw retained bundle, per-variant binary
identities, original metadata/startup, derived bundle, application endpoint,
bootstrap files and common application SDK image are all bound by hashes.
Additional shared dependencies are added only where the prefix lacked them;
no existing bridge implementation library or driver is substituted.

These commands are prepared **for the parent after the actual-catching gate**:

```sh
python3 profiling/chimaera_m5/stage.py --legacy-original \
  --runtime .chimaera-m5/original/runtime \
  --bundle .chimaera-m5/original/guest.tar.gz \
  --bootstrap ros/chimaera_ros_ws/src/chimaera_ros_bridge/chimaera_ros \
  --common-runtime .chimaera-m5/runtime \
  --endpoint .chimaera-m5/build-benchmark/latency_endpoint \
  --output .chimaera-m5/matched/original_m5

python3 profiling/chimaera_m5/stage.py --legacy-implementation streamed_m5 \
  --runtime .chimaera-rewrite/20261020/runtime \
  --bundle .chimaera-rewrite/20261020/guest.tar.gz \
  --legacy-source .chimaera-rewrite/20261020/source.tar.gz \
  --bootstrap ros/chimaera_ros_ws/src/chimaera_ros_bridge/chimaera_ros \
  --common-runtime .chimaera-m5/runtime \
  --endpoint .chimaera-m5/build-benchmark/latency_endpoint \
  --output .chimaera-m5/matched/streamed_m5

python3 profiling/chimaera_m5/stage.py --legacy-implementation initialized_m5 \
  --runtime .chimaera-rewrite/20261020/guest-v2-final/opt/chimaera/latency/app \
  --bundle .chimaera-rewrite/20261020/guest-v2-final.tar.gz \
  --legacy-source .chimaera-rewrite/20261020/source-v2-final.tar.gz \
  --bootstrap ros/chimaera_ros_ws/src/chimaera_ros_bridge/chimaera_ros \
  --common-runtime .chimaera-m5/runtime \
  --endpoint .chimaera-m5/build-benchmark/latency_endpoint \
  --output .chimaera-m5/matched/initialized_m5

python3 profiling/chimaera_m5/stage.py --legacy-implementation initialized_m5_ready \
  --runtime .chimaera-rewrite/20261020/runtime-v2 \
  --bundle .chimaera-rewrite/20261020/guest-v2-ready.tar.gz \
  --legacy-source .chimaera-m5/initialized-m5-ready/source-provenance.json \
  --bootstrap ros/chimaera_ros_ws/src/chimaera_ros_bridge/chimaera_ros \
  --common-runtime .chimaera-m5/runtime \
  --endpoint .chimaera-m5/build-benchmark/latency_endpoint \
  --output .chimaera-m5/matched/initialized_m5_ready
```

The production `validate_bundle` wrapper is active only for an explicitly
selected, label-matched legacy pair using `m5`/`bridge`. All modern selections
use the production validator. A streamed pair cannot pass as original or final
initialized, even though their legacy JSON markers have identical bytes. These
are identity checks, not runtime compatibility assertions. After the parent
actual-catching gate, qualify the main three legacy identities under KVM with both policies and the
verifier in a separate cohort:

```sh
python3 profiling/chimaera_m5/matrix.py --stage smoke \
  --variants original_m5 streamed_m5 initialized_m5_ready --policies lossless latest \
  --output .chimaera-m5/legacy-qualification.json
python3 profiling/chimaera_m5/matrix.py --stage smoke \
  --variants original_m5 streamed_m5 initialized_m5_ready --policies lossless --correctness \
  --output .chimaera-m5/legacy-verified.json
```

Run it with the same `run.py --lock ... --gate ...` invocation below, and retain
every failed attempt. Do not interpret lossless delivery as established until
that run completes with the required counts. Missing protocol markers alone no
longer block the three registered legacy pairs once staged and hash-checked.
The integrated-DMA bundle already records protocol3/DMA ABI1/protocol2.
The fallback source preparation below selects the pre-streamed commit; it does not establish exact
historical binary source identity:

```sh
python3 profiling/chimaera_m5/reconstruct.py --implementation original_m5 \
  --output .chimaera-m5/reconstructed/original_m5
```

Copy `selections.example.json` into `.chimaera-m5/selections.json`, confirm the
built endpoint SHA256, and pin actual SDK/resource limits and paths **as visible
inside the environment that will run the suite**. Freeze after all builds and
staging are finished:

```sh
python3 profiling/chimaera_m5/freeze.py .chimaera-m5/selections.json \
  --root "$PWD" --output .chimaera-m5/selection.lock.json
```

Freeze hashes source provenance, all runtime files, relevant guest-bundle member
bytes, the entire bundle, simulator/config, benchmark/orchestration code, disk,
kernel and modules. It streams large files. Missing/unmatched selections remain
`ready:false` with reasons; matrix generation and reporting retain them.
For m5ops the module is `.chimaera-m5/kernel/chimaera_m5.ko`, built for
5.15.180-chimaera; it is passed as `m5_module`, never `dma_module`.
Parent-owned `latency.stage_guest()` now propagates `m5_abi:1` in source; refresh
the installed runtime before staging its candidate bundle. Freeze still requires
that metadata and does not weaken direct-backend validation.

Large body cohorts include serialization/RTPS/CDDS headroom:0.5MiB body uses a
1MiB DDS packet cap,1MiB body uses2MiB,8MiB body uses9MiB minus4KiB. The largest
cohort uses64MiB local SHM and9MiB registered payload slots. Retained DMA
selections request `buffer_mb=9 receive_slots=16 transmit_slots=4`; the new m5
driver has9MiB bulk slots. Profile/pool changes are recorded experimental settings,
not the archived historical8MiB setup. Unsupported original backends must fail
visibly. A completed maximum-size receipt alone does not establish sustained load.

## Post-gate commands

The parent creates a gate JSON only after announcing an actual successful KVM
robot catching experiment. Required fields are `experiment:actual_ball_catching`,
`cpu_type:kvm`, `status:pass`, `physical_host` matching selection, a nonempty
`announced_by_parent`, and `evidence`, an array of `{path,bytes,sha256}` records
for the actual catch results. `gate.example.json` is deliberately pending and
cannot authorize execution. The runner verifies evidence hashes before any
runtime import. This is an operator attestation tied to retained evidence; a
synthetic latency endpoint is not an actual ball-catching experiment.

```sh
python3 -m unittest discover -s profiling/chimaera_m5 -p 'test_*.py' -v

python3 profiling/chimaera_m5/matrix.py --stage smoke \
  --output .chimaera-m5/smoke-cases.json
python3 profiling/chimaera_m5/matrix.py --stage smoke --correctness \
  --output .chimaera-m5/verified-cases.json
python3 profiling/chimaera_m5/matrix.py --stage all-prior --repeats 1 \
  --output .chimaera-m5/all-prior-first-pass.json
python3 profiling/chimaera_m5/matrix.py --stage all-prior \
  --output .chimaera-m5/all-prior-cases.json
python3 profiling/chimaera_m5/matrix.py --stage capacity \
  --output .chimaera-m5/capacity-cases.json
python3 profiling/chimaera_m5/matrix.py --stage tails \
  --output .chimaera-m5/tail-cases.json

PYTHONPATH=ros/chimaera_ros_ws/src/chimaera_ros_bridge:$PYTHONPATH \
python3 profiling/chimaera_m5/run.py .chimaera-m5/smoke-cases.json \
  --lock .chimaera-m5/selection.lock.json --gate .chimaera-m5/catch-pass.json \
  --output .chimaera-m5/smoke-runs
python3 profiling/chimaera_m5/aggregate.py .chimaera-m5/smoke-runs \
  --output .chimaera-m5/smoke-report
```

Use the same runner invocation for the other matrices, with distinct output
directories. `--cases CASE_NAME ...` selects an explicit subset while every
unstarted case stays `not_run`. `--append` resumes the identical frozen inputs
and skips existing attempt directories, including failures. It never overwrites
or silently retries failed evidence; use a new output directory for a retry.
The parent must serialize this runner with every other session using the fixed
transport sockets; the runner's advisory lock coordinates only other copies
of itself. No script here starts a container.

Default all-prior performance is3repeats ×2policies ×8implementations ×5modes
=240cases,48 simulated. Historical detailed runs cost about an hour per case;
the one-repeat first pass is explicitly provisional. Native modes are free and
stepped with SHM, plus both modes with UDP as separate context. Their DDS profile
differs, so UDP controls never substitute for required matched SHM controls.
Capacity defaults to current scheduling versus direct, lossless, one repetition;
the matrix records all six deferred implementations. Use `--variants` to extend
that explicit sweep to all eight when budget permits. Final tails default to
five8s windows for10,000 controls per stream, with actual counts checked. These
are staged plans; generation is not evidence of execution.

Promote an observed capacity knee without changing the baseline ball traffic:

```sh
python3 profiling/chimaera_m5/matrix.py --stage tails \
  --bulk-bytes 1048576 --bulk-mib-s 8 --bulk-burst 4 --bulk-direction both \
  --output .chimaera-m5/tail-knee-cases.json
```

Bulk source windows are at least three burst periods, so sparse8MiB/burst4
loads at2MiB/s require48simulated seconds. A2s source window would publish none
of that measured load after warmup. Observation windows align to complete bulk
burst periods inside the trimmed interval to avoid phase-biased byte rates.
These expensive sparse cells are explicit later stages. Where that extends a
cell past the standard reference duration, aggregation marks a missing
duration-matched control-only reference; prepare a separate control-only/ball
matrix with the same `--duration` before interpreting that contrast.

The capacity grid includes control-only and ball baseline cells for each
distinct DDS packet-size profile. Use `--cells` to stage explicit subsets and
`--variants` to expand beyond the current/direct pair.

## Reporting contract

`aggregate.py` exports JSON and CSV including every planned failure, cancelled
attempt, missing case, corrupt record and unplanned attempt. It recomputes from
raw events, joins publication times without altering the CSV, and checks source
identity, byte counts, timestamps, configuration, native SHM controls, deployed
DDS XML, artifact identity, physical host and active CPU provenance. Detailed
geometry is checked against gem5 config.ini:2TimingSimpleCPU,3GHz,16KiB L1I/D,
256KiB L2,MESI_TWO_LEVEL,DDR3_1600,3GiB. KVM origin is iteration evidence only.

Finite source-cohort receipts (including drain) per source-second and goodput
from fixed half-open callback-time observation intervals are separate fields.
Interior observation trims0.25s at both ends;0.25s bins retain their actual
duration and do not include drain. Body bytes and serialized event bytes are
separate; stereo pair and eye byte totals overlap. Missing sources and receipts,
duplicates, order, callback latency/age, source lag, stereo age/skew and
censored identities remain visible. Native clock resolution is1ms.

Application sent-but-unreceived backlog and oldest outstanding scheduled age
are reconstructed over observation time, including never-delivered messages.
They are **not** internal device queue/credit/wakeup measurements; those remain
unavailable. `bounded_observation` is a finite-run screening flag, never proof
of maximum capacity. Guest instruction/memory counter dumps retain exact scopes
and aliases; they are not relabeled as source-window-only work or summed across
overlapping dumps. Whole-attempt wall cost and runtime execution wall cost are
separate from simulation-time delay; the former includes artifact verification.

Per-window p50/p95/p99/max and counts remain explicit. After at least five
independent matched windows and10,000 observations, a one-sided bootstrap bound
on the **mean paired per-window p99 difference** can support observed
non-regression, with no added deadline misses. This is not a pooled p99 bound,
absolute worst case, or multiple-comparison-corrected result. The predeclared
control age threshold is10ms and camera age reference33.333334ms. Lossless and
latest, correctness and performance, and KVM and detailed cohorts remain distinct.
Functional m5ops excludes payload DMA modeling; this changes modeled memory
load as well as implementation overhead.

Archive historical context independently, keeping actual verifier values:

```sh
python3 profiling/chimaera_m5/history.py \
  .chimaera-rewrite/20261020/baseline \
  .chimaera-rewrite/20261020/candidate-v1 \
  .chimaera-rewrite/20261020/candidate-v2 \
  --cohort historical-detailed-pilots --output .chimaera-m5/history-pilots.json
```

Use separate cohort labels for final bridge/DMA acceptance, original DDS,
copy reduction, scheduling wait-only, final scheduling, and native UDP. Missing
historical `verify_payload` is unknown, not implicitly disabled. Frozen archives
and historical reports do not enter newly matched percentage comparisons.
