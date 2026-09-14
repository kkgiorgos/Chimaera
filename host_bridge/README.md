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

## Time Sync
We have two interfaces: one with a gem5 instance and one with a gazebo instance.
Gazebo should already be running because we will attach to it.
gem5 is influenced by a socket connection controlled by the gem5 config.

We need a way to give two both of them a signal to proceed by 1 sync period
which is the same as x ticks in gem5 and y steps in gazebo.

Note: Gazebo time steps are defined in the world meaning it should also
be parametrized in our bridge

So we give the signal to progress and then we also need a way to poll
for completion (or be notified).

This happens in a loop and then we can insert the data exchange
functionality at specified points.

