# Guest Bridge

This is a very basic implementation of the process on the guest that
handles the data queues (exchanging data with the host and syncing
the clock).

This ROS node has the special requirement that it needs to be built
against the gem5 library in order to use our custom m5 ops.

edit_disk.sh is used to move the compiled binary to the image that we use.
