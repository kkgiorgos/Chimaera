IMG=./x86-ubuntu-22.04-ros-humble.img

qemu-img resize ${IMG} 16G

sudo losetup -d "$LOOP"

LOOPDEV="$(sudo losetup -Pf --show ${IMG})"

lsblk "$LOOPDEV"

sudo growpart "$LOOPDEV" 2
sudo e2fsck -f "${LOOPDEV}p2"
sudo resize2fs "${LOOPDEV}p2"

sudo mount "${LOOPDEV}p2" /mnt/img

cp ./guest_net_setup.sh /mnt/img/home/gem5/

sync
sudo umount /mnt/img
sudo losetup -d "${LOOPDEV}"

