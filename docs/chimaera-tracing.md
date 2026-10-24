# Chimaera message tracing

Chimaera provides opt-in message latency tracing independently of application
code. Ball-catching memory experiments enable it by default with `fixed.trace`;
set that field to `false` to disable collection. Native preflights and earlier
recordings have no Chimaera latency measurements.

For another application, set these fields in its Chimaera session manifest:

```json
"bridge": {
  "trace": true,
  "trace_directory": "results/my-run",
  "trace_clock_file": "results/my-run/trace-clock.bin",
  "simulation_time_from_readfile": true
}
```

Paths resolve relative to the manifest. Use a fresh clock path for each session;
the coordinator creates it exclusively to prevent mixing clocks from different
runs. Keep the usual route, simulator and deployment configuration. The gem5
configuration must supply the `CHIMAERA_TIME_ORIGIN_NS` readfile marker, as in the
ball-catching configuration. Both sides must use rebuilt runtime libraries.
The runner stages the gem5 clock/export library and configures the participants;
applications do not need timestamp fields, recorders or extra ROS topics.

Fast DDS sessions require the current pinned RMW overlay from
[`middleware/prepare_rmw.py`](../middleware/prepare_rmw.py). Reprepare into a new
source directory, rebuild `chimaera_dds_transport`, `rmw_fastrtps_shared_cpp`,
`rmw_fastrtps_cpp` and `chimaera_ros_bridge`, and restage the guest. The
`chimaera_rmw_trace_abi` marker rejects older routing-only overlays. The runtime
also requires `chimaera_dds_trace_transport_abi=1`, so an older publication/take
tracer cannot silently pass as transport instrumentation. Serialized
bridge sessions require the rebuilt host and guest bridges but do not require
the patched RMW for tracing.

## Measurements

Only configured application topics are recorded, separately by direction:

- **Fast DDS transport:** serialized RTPS data entering the Chimaera transport
  on an accepted send to complete sample arrival at its receiving transport,
  before `OnDataReceived` delivers it to DDS. Source serialization,
  deserialization, executor waiting and application callbacks are outside these
  boundaries. Transport queues, worker scheduling, synchronization and fragment
  pacing remain part of transport residence. This measures the transport runtime,
  not isolated physical wire time or device service time.
- **Fast DDS delivery:** RMW publication entry to successful RMW take. This
  retains the earlier measurement, including serialization, DDS processing,
  deserialization and executor waiting. It is reported separately. A received
  sample can have transport timing even if QoS replaces it before an executor
  takes it. Message data, source timestamps, identities and QoS are unchanged.
- **Serialized ROS bridge:** outgoing serialized subscription callback entry to
  incoming bridge publication entry. This includes bridge copying, pairing,
  replacement queues, transport and synchronization, but excludes the source
  and destination applications' local DDS hops. An optional `CBT1` transport
  envelope identifies the sample and wraps the unchanged serialized ROS frame.
  Both peers must understand this envelope. Existing untraced frames remain
  supported, including paired stereo frames.

The serialized bridge currently records delivery only; it does not acquire
Fast DDS transport measurements through a fallback. These boundaries differ;
compare matching measurement definitions. None includes receiving callback work.
Observation age, controller computation and DRAM latency retain their existing
separate definitions.

RTPS writer GUID and sequence number join transport events to selected RMW
publications without inspecting application payloads. DATA and DATA_FRAG are
handled in both byte orders. A fragmented sample arrives only when all fragments
are present at one receiver; incomplete fanout peers cannot combine into a
receipt. Retransmissions do not become new publications. Raw transport metadata
without a selected source identity does not enter per-topic statistics.

Receive timestamps are captured before DDS dispatch and trace formatting/export.
An m5ops batch uses its common availability timestamp, excluding DDS work on
earlier datagrams in that batch. Timestamp reads use a separate lock from trace
exports. Send exports occur after the accepted send, retaining its entry time.
These choices keep export work out of that event's timestamp interval; the
instrumentation still adds execution and can affect subsequent traffic.

Guest timestamps use gem5 time minus the supplied origin. Host timestamps read
a process-shared logical clock advanced by the coordinator at physics and guest
completion boundaries. Their resolution is the synchronization interval, not
host wall time. Restored sessions align guest completion to the replayed physics
origin; delayed Gazebo statistics cannot roll the clock backward. These data do
not isolate socket or m5 operation execution time. The report records
`clock_resolution_ns` when the runner knows it. A sub-interval value does not
imply sub-interval host-clock accuracy; zero can mean both boundaries were
observed in the same synchronization interval.

Tracing is disabled by default in Chimaera. Enabled hooks add clock reads,
identity bookkeeping and synchronous trace export, which can affect modeled
execution and host throughput. Compare experiments with the same tracing
setting. Disabling tracing preserves the original RMW write path and bridge
wire frames. Records stream to disk rather than accumulating an unbounded
in-memory capture.

## Collection and presentation

Host processes write `chimaera-trace-host-PID.jsonl` in `trace_directory`. Guest
processes export `chimaera-trace-guest-PID.jsonl` directly into gem5's output
directory through the standard m5 file operation. No benchmark telemetry or
shutdown drain is needed to export completed trace events. In-flight writes can
still be absent when a run stops; unmatched identities are reported explicitly.

The reusable analyzer accepts these files plus the route configuration:

```sh
PYTHONPATH=ros/chimaera_ros_ws/src/chimaera_ros_bridge \
  python3 -m chimaera_ros.tracing results/my-run \
  --routes results/routes.json --start 1.0 --end 2.5 \
  --clock-resolution-ns 500000 --output results/my-run/transport_latency.json
```

It searches the run directory and its `gem5/` child. Source publication times
select the inclusive launch-to-evaluation window; receipts after evaluation are
censored. `--source-end` can end the source window before a receipt drain.
Schema 2 keeps three distinct stages for each topic:

- `transport`: first accepted transport send to complete arrival; its own sent,
  arrived, missing and fragment counters are independent of executor takes.
- `delivery`: the original publication-to-take or bridge delivery measurement.
- `serialization_and_send` and `receive_to_take`: per-message differences before
  and after transport, respectively. The latter includes DDS processing,
  deserialization and executor waiting, matched to the same receiving process.

The stream's top-level boundary, counters, `latency` and `time_trace` now all
refer only to transport. Publication/take counters remain under `delivery`.
Old recordings retain delivery statistics
and show transport as **not recorded**. Its value cannot be reconstructed by
subtracting processing time or percentiles.

Per-topic summaries include min, median, mean, P95, P99, maximum,
published/received counts, missing receipts, duplicates, unmatched identities
and invalid clocks. Percentiles use all matched nonnegative samples. Missing
receipts never become zero latency; negative delays are excluded and mark the
trace invalid. Malformed records and duplicate source identities fail analysis.
For topics with multiple receiving subscriptions, the first take per source
sample defines latency; additional takes count as duplicate/fanout receipts.
Plots retain at most 2,000 samples per topic without changing statistics.

Checkpoint users must preserve guest export prefixes alongside the snapshot:
`chimaera_ros.tracing.restore_prefix(checkpoint_directory, new_run_directory)`
copies the trace files before restoration so saved guest file offsets remain
valid. Ball-catching does this automatically and includes those prefixes in
checkpoint integrity checks. Prepare fresh checkpoints after upgrading.

Ball-catching writes `transport_latency.json`, includes it in `benchmark.json`
and the offline report, and retains all earlier outcome, processing, cadence and
memory statistics. Its dashboard shows each topic's boundary, distributions,
delivery counts, trace quality and selectable time trace; the comparison table
also shows effort transport latency. The dashboard selects transport or delivery
explicitly and shows the stages around transport. Older results display
unavailable transport values instead of presenting delivery as transport.

The synthetic benchmark uses the same hooks and analyzer with `--trace` on a
fresh-boot Chimaera Fast DDS run. For example, add `--trace` to
`chimaera_latency run --mode chimaera --middleware-transport fastdds ...`.
Its staged bundle must advertise transport tracing ABI 1 and include the gem5
clock; older bundles are rejected before boot. It writes the same
`transport_latency.json` and embeds it in `summary.json`. Source publications
stop at the measurement end, while arrivals may be observed through the drain.
Publish-to-callback benchmark metrics retain their original definition.
The aggregate report adds a separate transport table and
`transport-latency-report.csv`, using the same per-topic transport statistics.

Compare the same boundaries, clock resolution, hardware, transport/copy mode,
QoS, payload/traffic mix and tracing settings. Application load can cause real
transport contention; it is not removed by subtracting unrelated work.
The native baseline comparison does not subtract traced Chimaera callback
measurements from untraced controls.

Existing frozen experiments continue to use their saved runtimes. Fresh SDK,
application and readiness checkpoints are required to collect these new events.

## Validation

The [24 October transport validation](measurements/transport-tracing-2026-10-24/README.md)
records delayed-executor, fragmented-image, browser, workflow and real KVM m5ops
checks for the new transport boundaries.

On 23 October 2026, isolated ROS Humble SDK builds completed for the DDS library,
pinned RMW overlay, host/guest bridges and Gazebo coordinator. All five DDS
CTest targets and all eighteen bridge CTest targets passed, including real
native typed/serialized publication identity joins, tracing-enabled stereo
replacement/freshness, and shared-clock output against paused Fortress physics.
The ball-catching and middleware Python suites passed 154 tests with two skips;
the tracing analyzer has eleven dedicated checks. Desktop and 390 px browser
checks used explicitly synthetic UI fixtures. No full gem5 ball-catching run or
restored gem5 trace capture was performed for this change.
