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
`chimaera_rmw_trace_abi` marker rejects older routing-only overlays. Serialized
bridge sessions require the rebuilt host and guest bridges but do not require
the patched RMW for tracing.

## Measurements

Only configured application topics are recorded, separately by direction:

- **Fast DDS:** immediately before the successful RMW write, including
  serialization, to successful receiving RMW take, including deserialization.
  The receive boundary precedes the application callback. DDS writer GUID and
  sequence number identify each sample. Typed, serialized, sequence and loaned
  RMW paths are hooked. Message data, source timestamps, DDS identities and QoS
  are unchanged.
- **Serialized ROS bridge:** outgoing serialized subscription callback entry to
  incoming bridge publication entry. This includes bridge copying, pairing,
  replacement queues, transport and synchronization, but excludes the source
  and destination applications' local DDS hops. An optional `CBT1` transport
  envelope identifies the sample and wraps the unchanged serialized ROS frame.
  Both peers must understand this envelope. Existing untraced frames remain
  supported, including paired stereo frames.

These boundaries differ; do not treat their percentiles as equivalent
application end-to-end measurements. Neither includes receiving callback work.
Observation age, controller computation and DRAM latency retain their existing
separate definitions.

Guest timestamps use gem5 time minus the supplied origin. Host timestamps read
a process-shared logical clock advanced by the coordinator at physics and guest
completion boundaries. Their resolution is the synchronization interval, not
host wall time. Restored sessions align guest completion to the replayed physics
origin; delayed Gazebo statistics cannot roll the clock backward. These data do
not isolate socket or m5 operation execution time.

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
  --output results/my-run/transport_latency.json
```

It searches the run directory and its `gem5/` child. Source publication times
select the inclusive launch-to-evaluation window; receipts after evaluation are
censored. Per-topic summaries include min, median, mean, P95, P99, maximum,
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
also shows effort-command latency. Older results display unavailable values.

## Validation

On 23 October 2026, isolated ROS Humble SDK builds completed for the DDS library,
pinned RMW overlay, host/guest bridges and Gazebo coordinator. All five DDS
CTest targets and all eighteen bridge CTest targets passed, including real
native typed/serialized publication identity joins, tracing-enabled stereo
replacement/freshness, and shared-clock output against paused Fortress physics.
The ball-catching and middleware Python suites passed 154 tests with two skips;
the tracing analyzer has eleven dedicated checks. Desktop and 390 px browser
checks used explicitly synthetic UI fixtures. No full gem5 ball-catching run or
restored gem5 trace capture was performed for this change.
