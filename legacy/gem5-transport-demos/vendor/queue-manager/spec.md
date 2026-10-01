# Queue Manager

This is a module that will attach to bigger systems. There are two
components to it. The one part is a serializer and the other is a deserializer.
Both have multiple queues that a higher level system sees and provide
one serial channel for a lower level system to use.

Both systems identify their queues with channel IDs. This is how an
external system will choose the queue it wants. They also have customizable
depths.

Each queue can operate concurrently with others but it can't have
concurrent accesses to itself.

## Serializer
The serializer side needs to work with snapshots. This means that
when an external command requests a serialization it expects the entire
contents of all the queues in a single bundle as it was when requested.
To do this efficiently we need two queues per channel at request time
with a locking mechanism we switch between the two do our processing on
the snapshot and completing our request while allowing producers
to enqueue new things for the next batch. Obviously once a bundle is
created it is returned to the lower level for transport.

## Deserializer
The deserializer side has no such constraint as it can just make the newly
received batch available as it goes. It is just a regular producer consumer
syncing issue that can't be avoided in any implementation. The deserialization
procedure is triggered externally by the lower level system when it has
received the bundle.
