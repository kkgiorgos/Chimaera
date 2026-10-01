# Mock Simulator Controller Interface

This is the abstract control loop running on host and guest sides.
At sync intervals data exchange happens between the two sides.
The data preparation/serialization/deserialization is handled by
higher level modules.

## Spec

### Guest Side
The limitation here is that this gets stopped by the host after an
interval duration has elapsed in order to maintain the co-simulation
syncing.

Therefore the guest has to poll for new data at its own intervals
that will not match the host. The lower level transfer protocol has
to be asynchronous as the guest will send stuff through and the host
will receive(examine) them after the guest has stopped for the interval,
and the guest will receive the host's data later.

Alternative the guest could poll for new data from host thus receiving
right after execution has resumed, but polling in general would cause
spinning that consumes resources affecting simulation fidelity.

A mix of the two approaches would be the best of both worlds.
This means that the guest polls the host at smaller intervals than
the sync interval (say 1/10 or 1/100).

At these points you receive if there's anything to receive. Whatever
you get is available to a consumer that will connect to the controller
whose job is to handle that data and expose it to the rest of the guest.
Also, at these points you send any data you already have, you get that
data from an external producer that handles the data the guest wants to
send.

These external handlers are not part of the controller interface.
They are generic implementations that support an interface to
request or submit data.

### Host Side
This controller has the responsibility of time syncing apart from
data transfer. It has a timing controller component. That component is
abstract as well (an implementation is simulator dependent).

The controller gives the signal for the guest sim to start and run for
one sync interval. It also gives the signal for the robotics simulator
to do the same. Once both have completed, it sends the data the external
producer on the host has got (same external handler as before), and
receives all data the guest had sent on the just done interval and gives
them to the consumer.


