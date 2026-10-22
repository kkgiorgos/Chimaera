# Chimaera DDS transport

The pinned RMW overlay calls this package's existing participant, endpoint,
wire-topic and disposal hooks. The socket transport remains the default.

Set `CHIMAERA_DDS_BACKEND=m5ops` on both host and guest processes to select the
direct functional backend. Keep `CHIMAERA_DDS_SOCKET`, `CHIMAERA_DDS_SIDE` and
`CHIMAERA_DDS_ROUTES` configured on both sides. Host participants exchange
payloads with their Router over separate control and bulk sockets. Guest
participants send CDDS frames directly through `chimaera::M5Endpoint` and receive
registered buffer loans directly into Fast DDS. The guest Router connection
carries channel and endpoint
registration metadata for coordinator readiness and explicit disposal.

Each opt-in host participant has independent control and bulk socket fds, owned
transmit FIFOs, writer threads, receive buffers and reader threads. The Fast DDS
send callback copies the complete CDDS frame into its lane FIFO and returns
without waiting for socket/router credit. Each lane permits at most 64 MiB and
128 records, counting the worker's in-flight frame as well as queued frames.
These are independent lane budgets; four 8 MiB samples plus framing fit in one
lane. One writer per lane preserves FIFO across data and Open/Close metadata.
Channel operations report local FIFO acceptance, not a router acknowledgement;
registration precedes later frames accepted on the same lane. Each channel
registers only on its assigned lane socket. Each guest participant owns
independent control and bulk endpoints,
receive pools, reader threads, cancellation eventfds and send locks. Guest readers
block indefinitely in `poll` on persistent device readiness and cancellation,
and retain each RX loan until `OnDataReceived` returns. The core m5 engine supplies
guest custom-transport
fanout and discovery; the coordinator owns cross-simulator admission.

In the opt-in backend, each Router client input pass and each output flush is
bounded to 256 KiB and 64 complete records. The output bound also applies to
immediate admission delivery. Partial input frames, queued output ownership and
byte offsets persist across passes, preserving per-flow FIFO order.

Opt-in routes use FNV-1a of the canonical wire topic (`host_topic` in route JSON)
to derive distinct locator ports, with collision rejection within the route set.
`sensor_msgs/msg/Image` and `sensor_msgs/msg/CompressedImage` default to bulk;
other routed types default to control. An optional route field
`"transport_class": "control"` or `"transport_class": "bulk"` overrides this
default, for example for a `ByteMultiArray` bulk stream. Other values and types
are rejected in the opt-in backend. Bulk locators set port bit `0x40000000`;
graph and discovery always use control. A wire topic cannot have conflicting
types or transport classes across aliases.
Classification does not inspect RTPS packet size or split combined RTPS packets.
The existing RMW hook ABI is unchanged.

The opt-in RTPS packet limit is 9 MiB minus the unchanged 52-byte CDDS header
(9,437,132 bytes), allowing an 8 MiB application body plus CDR/RTPS overhead to
fit in one transfer. The complete CDDS frame is at most 9 MiB (9,437,184 bytes);
bulk RX slots must be at least this size. The descriptor, frame parser and local
SHM transport share this limit. SHM uses at least a 36 MiB segment, avoiding
fragmentation from a smaller local UDP transport. Without the opt-in, locator
port 1 and the existing 1 MiB profile are preserved.

Guest DDS always uses address operations, including discovery during KVM startup
and runs which later switch to detailed CPUs. It initializes libm5's mapping
once before constructing endpoints and retains it across participant teardown.
The private endpoint archive's symbols are hidden inside the DDS library so
clock-library mappings and destructors cannot interpose on this mapping.
`CHIMAERA_M5_DEVICE` optionally overrides `/dev/chimaera-m5`. Notification device
startup errors are retried for at most 30 seconds during setup; payloads and
completions have no retry timer or periodic poll. Load the address-operation
mapping driver (`/dev/gem5_bridge`) before starting DDS processes. No device or
m5 operation is used by the host transport.

The host retains no Fast DDS pointer after the callback. A full FIFO (or failed
allocation) refuses data with `false` and retains no copy, without stopping the
transport. This is a datagram-like rejection: pinned Fast DDS 2.6.12 ignores the
transport callback result, so it does not cause an SDK-call retry. Reliable RTPS
may recover through heartbeat/ACKNACK; best-effort data may be lost. Direct guest
`send` still synchronously consumes the borrowed header/body spans, and its
backpressure refusal has the same callback-result limitation.

Host metadata enqueue failure stops both lane sockets, so a refused Close cannot
leave an active router channel behind. An Open failure rolls back the local
channel. A later socket write failure also stops the transport, including after
a partial frame. Stop interrupts socket I/O and wakes FIFO waiters; shutdown
joins all readers and writers before releasing queued frames and closing fds.
Accepted frames may be discarded on transport failure or shutdown. This queue
does not provide lossless backpressure or a delivery acknowledgement.

Guest device failures stop the transport and wake both readers. Channel
registration failures roll back local state. Closing a channel removes its local
receiver and waits for in-flight delivery before returning. Registration is
serialized per lane, and callback waits do not hold the registration lock. The
legacy single-socket transport and direct guest payload/metadata paths retain
their synchronous behavior.

The default build links the PIC `chimaera::m5_endpoint` target from
`GEM5_TRANSPORT_ROOT`. It uses C++20 privately and exports the existing C++17 RMW
interface. A socket-only build can set `CHIMAERA_DDS_BUILD_M5OPS=OFF` without a
guest libm5 dependency; it still supports opt-in host lane sockets and rejects
direct guest use explicitly. The endpoint target must not propagate
executable-only `-no-pie` flags into this shared library.
