# Bridge head-of-line diagnostic

This bounded structural probe builds the production DDS router together with
the transport's actual queue, DMA batch layout and host mailbox. It checks
FIFO behavior with a 1 MiB bulk body and 64-byte controls, independent input
connection progress during an incomplete bulk frame, and independent output
recipient progress while a bulk receiver is unread. It verifies complete bytes.
It does not run a simulated DMA device or measure latency/throughput.

Build in the Ubuntu 22.04 / ROS Humble SDK, with Fast DDS 2.6 and C++20:

```sh
source /opt/ros/humble/setup.bash
cmake -S profiling/chimaera_rewrite/head_of_line -B /tmp/chimaera-hol-build \
  -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/chimaera-hol-build -j2
/tmp/chimaera-hol-build/head_of_line_probe
```

Each run creates a private `/tmp/chimaera-hol-XXXXXX` socket/mailbox namespace.
Socket operations and the notification wait have three-second timeouts. The
temporary directory is removed when the executable finishes. An incomplete
stream/slow recipient is deliberately introduced; no sleep-duration latency
claim is made.

The retained 2026-10-22 run used source revision
`912ae01ec5b1f3bec0ef4427b8756d0a160b9f43`, plus these diagnostic files, and SDK
`chimaera:middleware-gem5` at
`sha256:c841ebb1000488c0010e21bc32a62b9d432b1a47e47f64e16501dc53bd66c8d4`.
Sources were mounted read-only, networking disabled, and temporary build storage
was private to the container. Results are in
[`docs/measurements/chimaera-bridge-throughput-2026-10-22`](../../../docs/measurements/chimaera-bridge-throughput-2026-10-22).

Equivalent isolated build/run from the repository root:

```sh
docker run --rm -i --network none --read-only --tmpfs /tmp:exec \
  -v "$PWD:/workspace:ro" chimaera:middleware-gem5 bash -s <<'BASH'
set -eo pipefail
source /opt/ros/humble/setup.bash
cmake -S /workspace/profiling/chimaera_rewrite/head_of_line \
  -B /tmp/hol-build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/hol-build -j2
/tmp/hol-build/head_of_line_probe
BASH
```

The same SDK rebuilt and passed all nine existing transport tests. Its packaged
custom libm5 was selected explicitly because this checkout has no built libm5:

```sh
cmake -S /workspace/gem5-transport -B /tmp/transport-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGEM5_M5_LIBRARY=/opt/chimaera/gem5/util/m5/build/x86/out/libm5.a
cmake --build /tmp/transport-build -j2
ctest --test-dir /tmp/transport-build --output-on-failure
```

Setup attempts: the first container invocation omitted stdin attachment and ran
no script; the next built successfully but its `/tmp` mount was non-executable,
so the executable did not run. The final invocation added `-i` and
`--tmpfs /tmp:exec`; it completed the probe and all transport checks. Neither
setup attempt produced a simulation or performance result.
