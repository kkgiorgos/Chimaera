# Host Bridge

This is a very basic implementation of the process on the host that
handles the data queues.

## Guest -> Host

For this we open a socket to which the custom gem5 op will write the
data it receives from the guest to.

For now as we are just creating proof-of-concept we just show some data
in the console.

In the future this will become a ROS node that will decode the incoming
data and place it into proper queues from where they will be assigned to
their ROS topics, etc...
