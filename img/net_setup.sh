sudo ip link add br0 type bridge
sudo ip addr add 192.168.100.1/24 dev br0
sudo ip link set br0 up
sudo ip tuntap add dev tap0 mode tap user "$USER"
sudo ip link set tap0 master br0
sudo ip link set tap0 up
