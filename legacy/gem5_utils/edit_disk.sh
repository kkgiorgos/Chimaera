LOOPDEV="$(sudo losetup -Pf --show ../gem5/resources/ubuntu-24.04-test.img)"
sudo mount "${LOOPDEV}p2" /mnt/img

cp ../gem5_binaries/m5op_test/test_bridge /mnt/img/home/gem5/

sync
sudo umount /mnt/img
sudo losetup -d "${LOOPDEV}"
