# Chimaera Fast DDS overlay

The pinned RMW overlay adds transport and endpoint hooks to ROS Humble's
`rmw_fastrtps_cpp`. The underlying Fast DDS library is unchanged. Without
`CHIMAERA_DDS_SOCKET`, the hooks are inactive. Generated upstream sources and
build products belong under the ignored `.chimaera-middleware/` tree.

Prepare the checksum-verified source:

```sh
python3 middleware/prepare_rmw.py --output .chimaera-middleware/rmw-source
```

In the Ubuntu 22.04 / ROS Humble SDK, build the transport and the two patched
packages, then source the resulting overlay before launching participating nodes:

```sh
source /opt/ros/humble/setup.bash
colcon build --base-paths ros/chimaera_ros_ws/src/chimaera_dds_transport \
  .chimaera-middleware/rmw-source --packages-select chimaera_dds_transport \
  rmw_fastrtps_shared_cpp rmw_fastrtps_cpp --merge-install \
  --build-base .chimaera-middleware/build --install-base .chimaera-middleware/install \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
```

Participating nodes use `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` and:

- `CHIMAERA_DDS_SOCKET`: local runtime socket.
- `CHIMAERA_DDS_SIDE`: `host` or `guest`.
- `CHIMAERA_DDS_ROUTES`: the normal Chimaera topic-route JSON file.

This is an experimental implementation. End-to-end acceptance and performance
results are tracked in `docs/chimaera-dds-middleware.md`.

For a self-contained SDK overlay, use the dedicated image recipe:

```sh
docker build --file middleware/Dockerfile --build-arg SDK_IMAGE=chimaera:orchestrator-sdk \
  --tag chimaera:dds-sdk .
```

Use that SDK tag when building the ball-catching application. Session manifests
select `bridge.middleware_transport: "fastdds"` and use the same ROS domain ID
on both sides. The coordinator processes disable their own custom participant
transport and own the side's runtime socket. Ordinary bridge sessions retain
separate domain IDs and the default `"bridge"` mode.

Routed participants use explicit disposal/session teardown rather than a
wall-time discovery lease: a guest may be paused for minutes during detailed
simulation. Finite deadline/lifespan/liveliness policies need separate
simulation-clock qualification; the initial acceptance uses the existing
application's default infinite policies. DDS fragmentation and per-topic QoS
remain active. Camera pairing occurs at the consumer by capture timestamp;
this transport does not provide a cross-topic DDS transaction.
For explicitly local profiles containing only SHM and loopback-whitelisted UDP,
the hook retains SHM with a 1 MiB RTPS packet limit and removes loopback UDP.
Otherwise UDP's approximately 64 KiB limit fragments each camera image even
when its destination uses Chimaera. Networked transport profiles are preserved.
Middleware-mode DMA controllers permit 8,192 queued packets, retaining their
32 MiB byte bound. The legacy 1,024-message limit can reject a fresh-boot DDS
discovery/reliability burst before normal application traffic begins. Legacy
bridge sessions keep that count limit. Per-side IPC runtimes have a separate
64 MiB byte bound; exceeding either bound fails the session explicitly.
In middleware mode, the original endpoints own QoS, history and freshness.
Legacy bridge queue coalescing and bridge-level stereo expiry are bypassed;
the existing ball-catching consumer still rejects expired or unmatched frames.

Router queues share owning `Packet` buffers, including local fanout. A packet
must remain immutable after submission. `Router::take()` transfers its owner to
the coordinator; `receive(Packet)` retains that owner until each matching socket
has accepted the bytes or disconnected. The bridge adopts received DMA loans,
so a receive slot is reposted only after the last socket queue releases it.
The raw-pointer `receive(data, size)` overload makes one defensive copy for
callers without an owner. Queue capacity still charges the complete packet per
recipient, even when those recipients share storage.

The participant sends the CDDS header and RTPS payload using scatter/gather I/O.
The router reads each validated payload directly into its final allocation.
These changes remove seven of the eight full-payload userspace copies previously
counted on the host-to-guest path; the host mailbox copy, socket-kernel copies,
DDS history and typed serialization/deserialization remain. Guest-to-host traffic
also retains TX staging and the paused host mailbox read. Rebuild the DDS library
and bridge together and restage the guest bundle when upgrading the `Packet` API.
The [KVM comparison](../docs/chimaera-dds-copy-reduction-2026-10-21.md) records
mixed latency results: lower means without the image verifier, higher means
with it, and variable tails. Reduced copying alone does not guarantee lower
callback delay under application CPU load.

The RMW overlay also carries `ros_discovery_info`, translates graph queries
through the configured aliases, checks endpoint message types and reports
endpoint creation/disposal to the runtime. Unselected application endpoints
continue to use the normal local transports. This implementation covers topic
publishers/subscribers; services, actions and checkpoint restoration are not
qualified by this work.

Run the single baseline with the rebuilt application overlay sourced:

```sh
python3 middleware/validate_ball.py --output .chimaera-middleware/ball-acceptance \
  --image /path/to/guest.img --kernel /path/to/kernel --gem5-root /path/to/gem5 \
  --cpu-type kvm --middleware fastdds --backend dma --dma-module /path/to/chimaera_dma.ko
```

The disk remains a read-only base for gem5's disposable COW overlay. Kernel and
DMA module must be a matching pair. Use a simulator containing `ed9c7a6`'s nested
interrupt acknowledgement fix; the older SDK binary can lose disk interrupts.
For diagnosis, `--clock-probe` adds a guest process that compares Linux sleep
and monotonic time with address-mode m5 time on each core. It exports
`baseline/gem5/chimaera-clock-probe.json`. Leave this option off for final
acceptance and latency measurements.

Stage the benchmark runtime, then run the matched matrix:

```sh
python3 -m chimaera_ros.latency stage --output .chimaera-middleware/latency-guest \
  --bundle .chimaera-middleware/latency-guest.tar.gz
python3 middleware/benchmark.py --output .chimaera-middleware/latency-acceptance \
  --physical-host YOUR_MACHINE \
  --runtime .chimaera-middleware/install --bundle .chimaera-middleware/latency-guest.tar.gz \
  --gem5-root /path/to/gem5 --image /path/to/guest.img --kernel /path/to/kernel \
  --dma-module /path/to/chimaera_dma.ko --cpu-type timing
```

The matrix uses the same compiled endpoint, RMW and transport libraries for
both implementations. It runs native-free/native-stepped controls, bidirectional
64-byte traffic at 97 Hz, and 480x360 stereo at 30 Hz with 250 Hz joint/control
and 1 kHz clock traffic. Reliable depth-128 delivery and deterministic image
verification accompany every image comparison. This benchmark policy is
separate from the application's existing sensor QoS, exercised by the catch.
The default window is two seconds, with 0.25 s warmup and 0.75 s drain. Every
attempt is retained, and the source patch/archive and binary hashes identify
the measured implementation. KVM mode is available for harness smoke checks;
TimingSimpleCPU is the default for latency qualification.
All matrix cases use the same SHM-only, 1 MiB local DDS profile, including the
native controls and legacy bridge, to keep packet-size tuning out of the
middleware handoff comparison.
Guest staging copies the selected RMW and linked runtime libraries into the
bundle's `lib/` directory and records their hashes in `runtime-libraries.json`.
The guest keeps its own glibc and loader. This avoids depending on source/build
paths or on a preinstalled unpatched RMW. Old Fast DDS bundles without these
libraries are rejected before boot.
Completed matrices also export `evidence/middleware-comparison.json` and `.csv`.
The comparison rechecks native references, runtime/DDS fingerprints, CPU geometry
and simulator identity. Percentage reductions require complete, ordered, unique
delivery across the workload and equal source counts for the compared stream.
Regenerate them with `python3 middleware/report.py PATH/evidence/attempts.json
--output PATH/evidence`.

Add a second host-only reference with DDS shared memory disabled:

```sh
python3 middleware/native_baseline.py \
  --reference PATH/evidence/attempts.json \
  --output .chimaera-middleware/native-udp \
  --runtime .chimaera-middleware/install --physical-host YOUR_MACHINE
```

Run this on the same physical host as the reference, with its exact compiled
runtime sourced. It derives both workloads and native clock modes from the
retained matrix and runs no VM. The profile uses only UDPv4 on `127.0.0.1`,
disables builtin transports and DDS data-sharing, and sets `ROS_LOCALHOST_ONLY=0`
so Humble does not add SHM. Traffic, QoS, clock settings and runtime hashes are
checked against the SHM references. The UDP packet limit is 65,500 bytes, versus
1 MiB for the existing SHM profile; this compares complete DDS transport
configurations, including fragmentation. Results are exported separately as
`native-attempts.json` and `native-comparison.json`/`.csv`. They do not replace
the SHM-matched controls in the gem5 comparison. Use `--report-only` with the
same arguments to regenerate comparisons without rerunning the cases.
