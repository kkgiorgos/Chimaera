# Ball-catching controller feedback diagnosis (2026-10-23)

The stopped experiment `df02147c380b42eb83782635e84e78e7` delivered no
arm commands while perception continued. The controller's clock and timer
were running; its joint-feedback subscription was not receiving usable data.
The missing commands were a feedback transport problem, before torque computation.

The collected baseline is on the Pi, inside
`chimaera-orchestrator-controller-1`, at
`/data/results/df02147c380b42eb83782635e84e78e7/attempts/bbdf4f5bffd34fffb7b888caf23bdcda/suite/case-000-attempt-001`.
It contains 30 perception records, no control records, and no delivered commands.
Perception completion timestamps use the expected gem5 origin.
Retrieved baseline logs and diagnostic probes are retained locally under
`results/ball-catching-controller-feedback-20261023/probes/`.

## Cause and change

Fast DDS endpoint discovery uses wall-clock reliability timers on the host.
A detailed guest can spend several wall seconds advancing one 500 microsecond
interval. Host HEARTBEAT and ACKNACK repeats accumulate while the guest is
paused, then consume its control lane and discovery processing. A diagnostic
probe transferred over 1,100 host control-lane packets within about 35 ms of
simulation; the recent packets were discovery HEARTBEAT/ACKNACK traffic.
An unchanged diagnostic probe received no joint feedback through 57 ms.

The m5ops host router now replaces semantically identical pending discovery
HEARTBEAT/ACKNACK packets with their newest copy. The key retains source and
destination identities, reader/writer IDs, flags, sequence ranges and
acknowledgement bitmaps; only RTPS counts and informational timestamps are
excluded from comparison. Changed state, DATA, DATA_FRAG, application-port
traffic and unrecognized packets retain FIFO delivery. The newest packet keeps
its original bytes and shared buffer ownership. Queue entries are removed only
after downstream acceptance.

The controller also reports initialization, first valid feedback, and blocked
control updates with received-message count, validity, and feedback age.

## Validation and limits

- The repeated-control regression fails against the original DDS library and
  passes against the patched library.
- All four DDS CTests pass, including middleware integration, with direct
  m5ops support enabled. The robot core/runtime CTests both pass.
- The ball-catching Python suite passes 144 tests, with two existing skips.
- The patched timing probe receives valid seven-joint feedback and delivers
  five arm commands, at approximately 32, 37.5, 41, 45 and 49 ms. It exports
  five control-timing records and one control record. The first control record
  uses feedback stamped 8.5 ms and takes about 0.29 ms of modeled computation.

The short unchanged/patched diagnostic probes both omit the temporary internal
DDS graph readiness helper, retaining the routed-endpoint barrier. They use
four TimingSimpleCPU cores, the original kernel/disk and guest application,
and no memory aggression. The patched probe changes the host DDS router; the
guest controller has temporary diagnostic instrumentation. This establishes
feedback and command delivery, not readiness, catching success, or sustainable
throughput. Further readiness validation was stopped at the user's request.
The eight-case experiment was not restarted.

## Clock bridging

The memory experiment already excludes `/clock` from host/guest routes and
reads gem5 time directly. Its Gazebo-to-ROS bridge now also omits `/clock`, and
the host state publisher uses no ROS simulation-clock subscription. Native
launches still supply ROS simulation time to their native robot nodes.
Launch tests cover both modes; existing route and runtime tests cover the
clock-free guest path.

Rebuild the worker images and prepare a new experiment to apply these changes.
Previously frozen images and bundles were preserved.
