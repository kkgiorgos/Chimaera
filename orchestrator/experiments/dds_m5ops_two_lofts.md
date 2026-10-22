# Two saved lofts with direct DDS + m5ops

Import [dds_m5ops_two_lofts.json](dds_m5ops_two_lofts.json) into **New experiment**.
The dashboard uses this configuration by default. **Validate** should show exactly
two cases, in order: `loft-3` and `loft-5`. Each runs once with zero memory pressure.

The launch settings come from the saved
[two-throw experiment](../../ros/ball_catching_ws/experiments/memory_contention_two_throws.json):
3 m/s from `[1.8, 0.04, 3]` along `[-1, 0, 2]`, and 5 m/s from
`[4.36, 0.04, 3]` along `[-1, 0, 1]`. Directions are normalized by the simulator.

The guest boots on KVM, then switches to four 3 GHz Timing cores with 32 KiB
8-way L1I/L1D caches, an 8 MiB 16-way shared L2 and the existing Ramulator
LPDDR5-6400 Orin memory-interface preset. The guest exposes 3 GiB RAM. These are
the existing robot experiment settings; the host worker CPU/RAM budgets are
separate from the guest hardware.

Both cases use 480×360 stereo at 30 Hz, planning at 30 Hz with a 60 ms minimum
successful-plan interval, effort control at 250 Hz, joint feedback at 1 kHz,
and 0.5 ms physics/coupling steps. The scoring window remains 1.5 simulated
seconds after launch, with no extra ready hold or pressure warmup. This is a
complete robot catch window, not the shortened synthetic bridge benchmark.
Guest DDS shared memory is 64 MiB, and image diagnostics are disabled.

Update the controller and all selected workers to the same source revision and
contents. Rebuild the controller/dashboard from `orchestrator/` with the existing
deployment environment:

```bash
docker compose --env-file .env up -d --build
```

Redeploy the selected supervisors through the dashboard. Choose one preparation
machine and one worker slot with 4 host CPUs and a 12 GiB RAM budget for a serial
first run. Leave the machine's disk/kernel fields and `fixed.m5_module` empty
to prepare the assets automatically. Preparation builds or reuses the SDK,
custom RMW, application and exact kernel/notification-driver pair, verifies the
pair with a boot probe, then freezes the guest bundle. Every case needs KVM;
readiness checkpoints are unavailable for this backend.

Queue a **new** experiment, for example `dds-m5ops-two-lofts`. Resuming an old
experiment retains its frozen transport and binaries. The configuration is ready
for use; these two Timing robot trials have not been run as part of preparing it.
The prior actual end-to-end KVM catch qualified the 3 m/s loft only.

To preview the two cases without building or simulating, from the repository root:

```bash
PYTHONPATH=orchestrator/backend python3 - <<'PY'
import json
from pathlib import Path
from controller import validate_config
root = Path.cwd()
config = json.loads((root / 'orchestrator/experiments/dds_m5ops_two_lofts.json').read_text())
_, cases = validate_config(root, config)
for case in cases:
    print(case['id'], case['label'])
PY
```
