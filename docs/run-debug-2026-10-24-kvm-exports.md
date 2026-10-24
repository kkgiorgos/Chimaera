# Preparation failure: concurrent KVM file exports

The preparation for experiment `d57b1aee3f86464fa0394e6158c85690` failed on
`ubuntu-george` at 14:09 EEST on 2026-10-24. Builds, kernel/driver ABI probing,
memory calibration and stream rate validation succeeded. Creating its readiness
checkpoint failed during two-core KVM startup with tracing enabled:

```text
src/base/output.cc:156: fatal: Attempted to close an unregistred file stream
```

Guest trace processes 121 and 177 were exporting separate files. KVM dispatched
address-mode m5 operations on separate CPU event queues without taking the device
event-queue lock. `pseudo_inst::writefile` opens, registers and closes streams in
the shared `simout` registry, which is not safe for concurrent modification.
Guest tracing's process-local mutex cannot protect that simulator-global state.

KVM local MMIO now migrates to the device event queue for the operation, using
the same serialization as its other MMIO accesses. This also protects m5 file
exports from different guest processes and output names.

## Validation

`orchestrator/kernel/smoke.py --export-stress --backend m5ops` boots the actual
guest disk/kernel/module, pins two independent exporters to guest CPUs 0 and 1,
and writes 512 records per process through address-mode m5 operations. The host
checks all 1,024 records, including offsets and exact contents, after guest exit.

- The deployed SDK `sha256:d1bb022e59e852219bbb39ddc4f96333c4b3c680975103180a9ca0c885d981fe`
  reproduced the exact unregistered-stream fatal. Error cleanup additionally
  reported a pybind11 GIL failure.
- The fixed simulator passed the same probe and all export-content checks.
  Its SHA-256 was `741bb216e98cfb42792d4e875b63576be5042384ff24d7dec2f49dd06b1361cc`.
  This validation used an incremental native build inside `chimaera:middleware-gem5`
  and ran the binary inside the deployed SDK; it did not replace the production SDK.
- The orchestrator/container suite passed **382 checks** locally and remotely:
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q orchestrator/tests containers/tests containers/ball-catching/tests`.
- A real Docker export of the installed simulator artifacts passed verification.
  Deliberately modifying the copied binary caused verification to reject it.
- Cache regressions cover bridge-only changes after BuildKit pruning, a missing
  Docker SDK slot, corruption recovery from a matching SDK, simulator/compiler
  input changes, and export failure preserving the previous checkpoint.

The successful [probe result](measurements/orchestrator-kvm-exports-2026-10-24/result.json)
is retained locally. Full before/after logs and build logs remain on the producer
under `.chimaera-orchestrator/validation-kvm-exports/`.

## Build reuse and deployment

Previously, bounded BuildKit pruning could remove the expensive simulator stage
even though its installed binaries remained in the final SDK image. A bridge or
middleware change invalidated the SDK slot and could compile gem5 again.

Preparation now keeps one verified, persistent simulator artifact checkpoint,
separately keyed by gem5 sources, the compiler profile/environment recipe and
simulator compilation script. SDK and application-dependency builds import those
artifacts through the existing prebuilt stage. Runtime/bridge/application changes
reuse them; simulator or compiler inputs invalidate them. Each file is hashed,
failed exports preserve the previous checkpoint, and BuildKit GC cannot remove
the installed artifact checkpoint. Missing/corrupt checkpoints can be recovered
from a matching installed SDK without compiling.

The producer checkout and supervisor were updated. This simulator fix requires
one new production SDK build because simulator source changed. Subsequent bridge
or application revisions reuse its simulator artifacts. No new production
experiment or full robot/checkpoint qualification was run. The failed experiment
retains its frozen source identity; align all selected worker/controller sources
and redeploy their supervisors before queuing a new experiment.
