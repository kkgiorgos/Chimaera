# Experimental functional middleware copies

This is a **separate memory-cost model**, not a default transport optimization.
The existing functional m5ops transport excludes timed payload DMA. This optional
experiment additionally executes large, scoped middleware memory copies/fills
on the simulator host, excluding their ordinary guest instructions and timed
memory accesses. Application computation outside those scopes remains native.

Compare the same preload library with
`CHIMAERA_FUNCTIONAL_MIDDLEWARE_COPIES=0` and `=1`. Use fresh guests and the same
CPU geometry, endpoint, DDS library, pools, source schedules and QoS. Enabling
the helper on an older simulator fails capability negotiation rather than
silently mixing memory-cost models. The simulator extension advertises
`CHM5_FEATURE_FUNCTIONAL_COPIES`; the existing notification module remains
compatible and no additional guest endpoint/receive pool/thread is created.

## Scope and ownership

`LD_PRELOAD` intercepts `memcpy`, `memmove`, and `memset`, but acceleration is
enabled only inside marked middleware scopes. The helper wraps normal RMW
publication, typed take/take-with-info, and serialized publication. The custom
DDS transport marks synchronous `OnDataReceived`. Standard ROS application
callbacks execute after those scopes end. Other RMW APIs and direct DDS user
listeners require separate qualification; this experiment targets the retained
typed stereo/control/Image workload.

Eligible operations are 64 KiB through 32 MiB. Smaller operations and overlapping
moves remain native. Before a functional operation, the helper writes one byte
per destination page with its final value so Linux resolves demand/COW faults.
Those instructions and memory accesses remain modeled. The synchronous handler
uses at most 64 KiB of host scratch storage and retains no guest address after
return. Copies and fills neither admit packets nor change credits or epochs.
A failed call may have written a prefix; the helper redoes the complete operation
natively. A comparison with model memory faults is rejected by the diagnostic
report instead of being treated as a clean functional-copy result.

The helper owns a private, process-lived address-op mapping; embedded m5 symbols
are hidden from clock and DDS libraries. Only the guest application command
receives the preload and mode environment, leaving the host and guest coordinator
outside this boundary. Constructor negotiation and a final stderr summary make
enabled mode explicit. Simulator statistics under
`board.chimaera_m5.functionalCopies` record calls, bytes, and memory faults even
if the application is stopped before library destructors execute. Exact dump
scopes may overlap and include warmup/drain/export; never sum them indiscriminately.

## Build and checks

Build inside the pinned Humble SDK after sourcing its ROS/RMW overlays:

```sh
cmake -S profiling/chimaera_m5/copy_offload -B COPY_BUILD \
  -DCMAKE_BUILD_TYPE=Release -DGEM5_ROOT="$PWD/gem5"
cmake --build COPY_BUILD -j1
ctest --test-dir COPY_BUILD --output-on-failure
```

`functional_copy_test` supplies a fake backend; it never executes native-host
m5 operations. It checks scope boundaries, threshold/large sizes, page alignment,
nested scopes, both overlapping move directions and fallback after a partial
write. Real simulator behavior must separately pass fresh-boot KVM full-byte
Image verification before timing comparisons.

`isolation_matrix.py --copy-modes` prepares matched guest/functional cases with
the split application executor. The runtime must contain the same helper and
DDS bytes in both host staging and guest app/loader directories; the helper
itself is preloaded only into the guest endpoint. Freeze new simulator/runtime/
bundle/source/tooling identities. `isolation_report.py` checks that functional
operation counters are present and positive only in enabled mode, with no
memory faults. These bounded transient diagnostics do not measure maximum
sustainable throughput or implement per-topic transport priorities/credits.

The actual robot configuration also requires the optional Ramulator backend.
Pass `RAMULATOR2_ROOT=/absolute/pinned/dependency` as a SCons argument when
building its simulator; exporting that environment variable alone does not
enable this repository's integration. Preserve the simulator/memory profile in
both mode arms. Tick-zero instantiation verifies configuration support but is
separate from actual KVM and timing qualification.
