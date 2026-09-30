#!/bin/bash
# Root PID 1 for the disk guest; the bridge owns the workbegin marker.
set -eo pipefail
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8 LC_ALL=C.UTF-8 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
stay_alive() { while true; do sleep 3600 & wait "$!" || true; done; }
failed() {
    status=$?
    trap - EXIT
    echo "CHIMAERA_GUEST_INIT_FAILED=$status" >&2
    /usr/local/bin/m5 --addr fail "$status" || true
    stay_alive
}
trap failed EXIT
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -o remount,rw /
if ! mountpoint -q /dev; then mount -t devtmpfs devtmpfs /dev; fi
mkdir -p /dev/pts /dev/shm /run /tmp
mount -t devpts devpts /dev/pts
mount -t tmpfs -o mode=1777 tmpfs /dev/shm
mount -t tmpfs -o mode=755 tmpfs /run
mount -t tmpfs -o mode=1777 tmpfs /tmp
python3 - <<'PY'
import fcntl
import socket
import struct
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    request = struct.pack('16sH', b'lo', 0)
    flags = struct.unpack('16sH', fcntl.ioctl(sock, 0x8913, request))[1]
    fcntl.ioctl(sock, 0x8914, struct.pack('16sH', b'lo', flags | 1))
print('CHIMAERA_GUEST_FILESYSTEMS_READY', flush=True)
PY
if grep -qw 'chimaera.probe=1' /proc/cmdline; then
    source /opt/ros/humble/setup.bash
    ldd /usr/local/bin/chimaera_wall_follow_controller
    ldd /usr/local/bin/chimaera_wall_follow_bridge
    test -c /dev/mem
    set +e
    timeout 3 /usr/local/bin/chimaera_wall_follow_controller --ros-args \
        --params-file /usr/local/share/chimaera/controller.yaml -p use_sim_time:=true
    status=$?
    set -e
    test "$status" -eq 124
    echo CHIMAERA_GUEST_PROBE_OK
    sync
    python3 -c 'import ctypes; ctypes.CDLL(None).reboot(0x4321fedc)'
    exit 1
fi
workload_pid=
trap 'if [[ -n "$workload_pid" ]]; then kill "$workload_pid" 2>/dev/null || true; fi' INT TERM
status=0
if /usr/local/bin/m5 --addr readfile > /run/readfile.partial && test -s /run/readfile.partial; then
    mv /run/readfile.partial /run/readfile.sh
    echo CHIMAERA_GUEST_READFILE_READY
    bash /run/readfile.sh &
    workload_pid=$!
    wait "$workload_pid" || status=$?
else
    status=1
    echo 'Guest readfile was empty or failed' >&2
fi
echo "CHIMAERA_GUEST_WORKLOAD_EXIT=$status"
if (( status )); then
    /usr/local/bin/m5 --addr fail "$status"
else
    /usr/local/bin/m5 --addr exit
fi
stay_alive
