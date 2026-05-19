IMG=../gem5/resources/x86-ubuntu-22.04-ros-humble.img

LOOPDEV="$(sudo losetup -Pf --show ${IMG})"
sudo mount "${LOOPDEV}p2" /mnt/img

cp -r ./install/ /mnt/img/home/gem5/

sync
sudo umount /mnt/img
sudo losetup -d "${LOOPDEV}"

