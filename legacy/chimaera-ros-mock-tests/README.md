# Archived universal ROS mock integration

These files preserve the former external talker/listener test through mock-sim.
They are no longer built, installed, or run by `chimaera_ros_bridge`. They used an
adapter between the callback-based mock controllers and gem5-transport's data
controller interface; the active bridge needs only gem5-transport.

The mock libraries and their standalone examples are in [../mock-sim](../mock-sim/README.md),
with their queue dependency in [../queue-manager](../queue-manager/README.md).
The adapter references the active ROS bridge headers and is historical test
source, rather than a self-contained frozen ROS package.
