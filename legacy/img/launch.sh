qemu-system-x86_64 \
    -hda x86-ubuntu-22.04-ros-humble.img \
    -m 4096 \
    -enable-kvm \
    -netdev tap,id=n1,ifname=tap0,script=no,downscript=no \
    -device virtio-net-pci,netdev=n1 \
    -nic user,model=virtio-net-pci

