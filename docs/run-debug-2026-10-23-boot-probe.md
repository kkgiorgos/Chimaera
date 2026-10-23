# Orchestrator boot probe failure, 2026-10-23

Experiment `3ac7eb41eb0d44978842f05ce9bcf2aa` failed preparation at
06:57 Europe/Athens. The previous external-module build correction worked:
Linux 5.15.180 and `chimaera_m5.ko` compiled, and the actual guest loaded the
module successfully, reporting `Chimaera m5 notification ABI 1, IRQ 17`.

## Cause

`orchestrator/kernel/smoke.py` required a complete `CHIMAERA_DRIVER_PROBE`
JSON line in the UART log after `simulator.run()` returned. The guest's PID 1
issues `m5 exit` after its workload completes, but that exit can precede draining
Linux's serial output queue. The saved serial log stopped mid-message, and the
host rejected the missing proof despite the clean exit.

An isolated rerun with the original check reproduced the failure. Using the same
assets with the corrected proof channel passed, confirming that the driver ABI
probe itself completes successfully.

## Correction

After all guest assertions succeed, the probe writes JSON to a guest file and
exports it synchronously with `m5 --addr writefile`. The host reads this exported
`driver-probe.json` after checking the simulator exit and serial disk errors.
Serial output remains diagnostic. A stale exported proof is removed before
simulation, and missing, malformed, non-object or wrong-backend proof is rejected.
Existing host checks still require the expected kernel release, ABI 1 and both
m5ops lanes before accepting preparation.

## Actual guest validation

Both reruns used disposable containers, read-only backing assets and separate
output directories under `/tmp/chimaera-smoke-debug-{before,after}`. No experiment
state or deployed service was changed. Host Docker/KVM access was available
through the execution tool's escalation path.

Assets from the failed run:

- Application image: `sha256:7c3172c36696c870c4a7027620f60156ab861c8a31b6de6fe0997a88eb9cb8dd`
- Guest disk: `.chimaera-orchestrator/assets/ball-catching-3dd3eb89828baa984593.img`
- Kernel/module directory: `.chimaera-orchestrator/assets/kernel-14ac7614f5619e88470f164431272f2302e606b7a02bd3d4c4a1b83606e6eabd`
- Kernel SHA-256: `ad1371a0ef614c4e9d6df4764852ab9ca04b304d8db11975adfe09e47db52b04`
- Module SHA-256: `67362657a59c538d77e3225d1d9ac76b972cc92341fae71e9fba20c83acec70c`

The corrected check returned `status: passed`, kernel release
`5.15.180-chimaera`, backend `m5ops`, ABI 1, lanes `[0, 1]` and simulator exit
code 0. Logs and exported proof are retained in
[`measurements/orchestrator-smoke-2026-10-23/`](measurements/orchestrator-smoke-2026-10-23/).
This validates actual guest boot and the driver ABI, not robot trials.

The focused kernel recipe/asset suite passed 66 tests. The full regression suite
passed 313 tests, with 16 existing tarfile deprecation warnings:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  orchestrator/tests containers/tests containers/ball-catching/tests
git diff --check
```

Regression cases cover truncated serial output with valid exported proof,
stale/missing proof, malformed JSON, incorrect backend, non-object JSON,
disk I/O errors and nonzero simulator exit codes.

## Deployment

Align controller and worker checkouts with this correction and redeploy the
orchestrator services before queuing a new experiment. The existing experiment
freezes its source identity, so retrying with changed sources will be rejected.
The kernel build inputs are unchanged by this correction; retain the cached
kernel/module pair and images.
