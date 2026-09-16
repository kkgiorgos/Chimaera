# gem5 Images

To run our tests we need an ubuntu image for FS gem5 simulation.

We document the process of creating one.

## ROS 2 Humble

We start with a base ubuntu 22.04 [image](https://resources.gem5.org/resources/x86-ubuntu-22.04-img?version=2.0.0) from gem5 resources.

Using the edit disk script we resize the image and the partitions and
add the guest network setup file (to get ssh access from the host).

We launch the image in QEMU. Setup networking inside the image and install
our packages (to ssh we need to setup networking on the host as well).

To access the internet in the guest use
```
dhclient -v ens3
```

Now we can install our packages
