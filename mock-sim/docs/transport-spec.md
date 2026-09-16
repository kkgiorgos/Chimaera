# Mock Simulator Transport Interface

We use this to test higher level data transfer modules against
a fake simulator.

To elaborate: instead of crossing a real arch sim boundary (like gem5)
we can use the same abstract primitives when developing higher level
things and transfer the data across a fake boundary here.

Separate host and guest side processes will run bare metal on the
development machine. They will exchange data through primitives built
here that abide to this spec which will be implemented on the real
simulator boundary.

When we want to move to the real simulator those processes keep the same
interface but the implementation switches to something simulator specific.

This enables faster testing of higher level functionality as spinning
up the simulator and setting up test environments takes too much time.

## Spec
All primitives are meant to be used sequentially.
They DO NOT allow multiple concurrent data transfers.
This means higher levels should serialise operations before sending
messages through the "barrier".

Host and guest side share the same interface but different underlying
implementation.

```
SendResult send(std::span<const std::byte> data);
ReceiveResult receive();
```

Result types contain error information.
