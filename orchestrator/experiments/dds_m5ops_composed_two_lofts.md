# Composed DDS+m5ops robot with reusable readiness

[dds_m5ops_composed_two_lofts.json](dds_m5ops_composed_two_lofts.json) is the
candidate for the orchestrator's two saved lofts. Default activation requires an
actual robot qualification and two successful detailed checkpoint restores; see
the [qualification record](../../docs/chimaera-m5ops-checkpoints-2026-10-23.md).

It uses two 3 GHz Timing cores, 16 KiB 8-way L1I/L1D caches, a 256 KiB 16-way
shared L2, 3 GiB RAM, and the existing LPDDR5-6400 memory-interface preset.
One robot process shares its DDS participant across separate effort and
perception/planning executors. Host worker CPU and memory limits are independent
of this simulated geometry.

`fixed.middleware_copy_mode: "functional"` excludes scoped large middleware
copy/fill costs from modeled guest execution. Select `"guest"` to model those
copies normally. Results label this boundary explicitly. Copy mode changes require
a new compatible checkpoint; the two modes must be reported separately.

With `fixed.readiness_checkpoint: true` and
`fixed.checkpoint_readiness_cpu: "kvm"`, preparation uses KVM through arm/camera
readiness, then switches to Timing before saving. Startup is outside the detailed
cost model. Restored throws run entirely on Timing and workers need no KVM.
Gazebo reconstructs the recorded pre-throw command history, validates the saved
robot state, and waits for fresh control/camera traffic before releasing a throw.
Record producers remain paused until all replacement record consumers confirm
receipt. Saved record prefixes retain their original sequence numbers.

The 3 and 5 m/s launch settings, 480×360 stereo at 30 Hz, 30 Hz planning,
250 Hz effort control, 1 kHz joint feedback, 0.5 ms coupling and 1.5 simulated
second task window are retained. Preparation is aggressor-free. Hardware,
camera/rate, execution layout, copy mode or preparation CPU changes require a
new checkpoint. Both saved lofts share one compatible readiness state.

Leave disk, kernel and `fixed.m5_module` overrides empty for managed assets.
Use one preparation slot with 4 host CPUs and 12 GiB RAM for an initial serial
run. Queue a new experiment after updating the controller and workers; existing
frozen experiments keep their original configuration and binaries.
