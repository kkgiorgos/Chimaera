# Chimaera latency profiling

These tools build an instrumented **copy** of the deployed universal bridge and
latency benchmark. They leave the production source, wire protocol, timing
settings and running deployment intact. The 2026-10-20 investigation uses the
deployed version-4 benchmark on `ubuntu-george`; this checkout's older version-3
runner is not interchangeable with it.

`instrument.py DEPLOYED_SOURCE NEW_OUTPUT` copies the bridge and transport, adds
`profile.hpp`, and inserts stage markers. Source anchors must match exactly;
source drift fails explicitly. Build the copied ROS package with the usual
Release CMake options and stage a new guest bundle. Point `GEM5_ROOT` at the
existing SDK and `GEM5_TRANSPORT_ROOT` at the copied transport. No gem5 rebuild
or guest-image deployment is needed.

`run_cases.py CASES.json NEW_OUTPUT` runs one case at a time with a fresh guest
boot. It accepts runtime, bundle, gem5, image and kernel paths. Case entries have
`name`, `variant` (`original`, `off` or `trace`) and `config` overrides. Optional
`guest_diagnostics: true` prints clock-source, timer and sleep diagnostics before
the guest application starts. Use a separate container with private `/tmp`, ROS
and Ignition discovery, a read-only base image, and read-only kernel. Never run
another native Chimaera session in the same socket namespace.

The trace records:

- Adapter callback, frame construction, queue submission/drain, controller batch
  handoff, incoming allocation/copy and ROS publish entry/return.
- Guest polling sleep, batch encode/decode and framed m5 send/receive spans.
- Host boundary work, gem5 phase, both settling windows, Gazebo control request
  and verified completion/statistics wait.

Both processes buffer up to one million records in memory. There is no trace
file I/O during the measured window. The guest exports its trace with
`m5_write_file` when the first post-drain benchmark telemetry reaches its bridge.
The host writes its trace on driver completion. Overflow fails explicitly. The
trace never enters ROS data messages. Synthetic sequence numbers and ordinary
typed-message header timestamps identify samples, including paired stereo frames.

The host records Linux steady wall time and the existing logical delivery clock.
The guest records its OS steady clock **and** the authoritative gem5 `rpns`
clock. Guest OS time is not host elapsed wall time. Logical host timestamps stay
constant while a simulator is paused; zero elapsed simulation time does not
mean zero CPU or wall cost. Small measured spans include clock-read and tracing
overhead. Always retain interleaved uninstrumented controls and rebuilt tracing-off
controls; results near queue saturation are particularly sensitive to tracing.

Run `analyze.py RUN_ROOT --output analysis.json` on collected cases. It joins
traces to raw source/receive events, selects by actual publication time, and
reports disjoint stages whose per-message sums equal end-to-end latency. Missing
stage paths and backwards timestamps are counted and excluded, never converted
to zero or clamped. Two-core KVM can exhibit sub-millisecond cross-core clock
skew; inspect coverage and use one-core checks before interpreting fine stages.
Pipeline counts include every measured source publication, including data absent
at the receiver. They indicate observed progress through the stack by the drain
cutoff, not proof of permanent packet loss.

`watch_physics.py CASE --seconds 30` observes the existing shared clock at 1 ms
host polling intervals. Its first observation of a final requested PreUpdate tick
can be compared with host `gazebo_request`/`gazebo_wait` markers. It is an upper
bound on when that PreUpdate occurred; scheduling can delay the observer. It does
not prove the final physics update or arbitrary callbacks have completed, and
must not replace the production completion barrier.

CPU sampling can run in a helper sharing **only the profiling container's** PID
namespace. Use host-only events such as `cycles:Hu` to exclude native KVM guest
execution. Resolve symbols against the stable main-container root (`/proc/1/root`
inside the shared namespace), disable inline expansion for the large gem5 binary,
and retain the raw `perf.data`. CPU sample percentages describe sampled active
cycles, not shares of elapsed wall time or simulation-time message latency.

Verification:

```bash
python3 -m unittest discover -s profiling/chimaera_latency -p 'test_*.py' -v
# In the isolated ROS build environment:
ctest --test-dir /profile/build --output-on-failure
```

The accompanying [investigation report](../../docs/chimaera-latency-profile-2026-10-20.md)
records the actual measurements, controls, provenance and limitations.
