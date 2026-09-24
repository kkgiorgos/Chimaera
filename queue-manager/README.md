# Queue manager

A standalone C++20 library for multiplexing bounded FIFO queues onto one byte
channel. It has no transport dependency; its bundles can be passed directly to
Chimaera's `Transport::send` and `Transport::receive` interfaces.

```sh
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

Embed with `add_subdirectory(queue-manager)` and link
`chimaera::queue_manager`. Tests default to off when embedded.

```cpp
#include <array>
#include <chimaera/queue_manager.hpp>

const std::array channels{
    chimaera::QueueChannel{10, 128},
    chimaera::QueueChannel{20, 64},
};
chimaera::QueueSerializer outgoing(channels);
chimaera::QueueDeserializer incoming(channels);

chimaera::QueueMessage payload{std::byte{0x01}, std::byte{0x02}};
if (!outgoing.try_enqueue(10, payload)) {
    // Queue full: retain the payload and retry after a snapshot.
}
auto bundle = outgoing.serialize();
// Send bundle through the lower-level transport, then at the receiver:
if (incoming.deserialize(bundle)) {
    auto message = incoming.try_dequeue(10);
}
```

Channel IDs are unsigned 32-bit values. Depths count messages, not bytes, and
must be positive. Empty messages are supported. Channel configuration is fixed
at construction; duplicate IDs are rejected. Sender and receiver depths may
differ. An unknown local channel throws `std::out_of_range`.

## Concurrency and snapshots

Every channel has its own mutex. Calls on the same channel are safe and execute
serially; calls on different channels can proceed concurrently. Serializer
enqueue and size calls also hold a shared snapshot gate. `serialize()` acquires
the exclusive gate, reserves the output buffer, and switches **all** channels
to their second queue at one atomic boundary. That boundary occurs during the
call, after any enqueues already holding the gate have completed. The gate is
released before encoding the detached queues, allowing producers to fill the
next batch. Concurrent serialization calls execute serially.

Each serializer channel can hold one snapshot of up to its configured depth
and one active queue of the same depth. `size()` reports only the active queue.
Messages are FIFO within each channel. Bundles group messages by ascending ID;
there is no cross-channel enqueue ordering guarantee. Output allocation failure
leaves queues unchanged. Once returned, the caller owns the bundle and must
retain it for any transport retries; the serializer has consumed its snapshot.
Payload size is not capped by the library, so the embedding system should apply
its transport and memory limits.

## Receiving and shutdown

`deserialize()` checks the complete framing and all channel IDs before inserting
anything. Invalid input throws `std::invalid_argument` without modifying queues.
Valid messages become available incrementally. If a queue fills, deserialization
waits until a consumer removes an item. Consumers must run concurrently when a
bundle can exceed available capacity. A full channel delays later records in
that bundle, but consumers on other channels remain free to run. Multiple
deserialization calls execute serially; callers must submit bundles in their
desired order. The input span must remain valid and unchanged during the call.
Allocation failure while receiving can leave an already-published prefix; do
not blindly replay such a bundle.

`try_dequeue()` returns `std::nullopt` for an empty queue. `wait_dequeue()` blocks
until a message is available or the queue is closed and drained. `close()` is
idempotent, wakes blocked consumers and deserializers, and prevents further
publication once it returns. Interrupted deserialization returns `false` and
may have published a prefix. Existing messages remain drainable. Call `close()`,
join all threads using the object, then destroy it. Serializer users likewise
must finish before destruction.

## Bundle format, version 1

All integers are unsigned and big-endian. There is no padding.

| Field | Bytes |
| --- | ---: |
| Magic `CQM` followed by version byte `0x01` | 4 |
| Total message count | 8 |
| Per message: channel ID | 4 |
| Per message: payload length | 8 |
| Per message: payload | payload length |

An empty snapshot is a 12-byte header with count zero. Empty channels have no
records. Receivers reject unsupported versions, unknown IDs, truncated fields
or payloads, inconsistent counts, and trailing bytes. Repeated channel IDs are
normal; their records are delivered in wire order. Framing is independent of
native integer layout and endianness.

## Mock simulator demo

The [mock controller demo](../mock-sim/README.md#interactive-controller-demo)
connects this library to the low-level mock controllers and their Unix socket
transport. Both host and guest expose channels 1 and 2 through
`send CHANNEL [TEXT]`. Its CTest integration test exercises actual separate
processes, bidirectional delivery, FIFO order, empty messages, and queue capacity.
