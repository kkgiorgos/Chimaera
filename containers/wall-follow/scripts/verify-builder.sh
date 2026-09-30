#!/usr/bin/env bash
set -euo pipefail
bash /opt/chimaera/scripts/verify-build-env.sh
/opt/chimaera/gem5/build/X86/gem5.opt --outdir=/tmp/gem5-build-check \
    /opt/chimaera/scripts/verify-gem5.py
nm -g --defined-only /opt/chimaera/gem5/util/m5/build/x86/out/libm5.a \
    > /tmp/libm5-symbols.txt
for symbol in m5_chimaera_send m5_chimaera_send_addr m5_chimaera_recv m5_chimaera_recv_addr m5_work_begin_addr; do
    grep -Eq "[[:space:]]${symbol}$" /tmp/libm5-symbols.txt
done
for binary in controller guest_bridge host_bridge; do
    case "$binary" in
        controller) package=wall_follow_robot ;;
        *) package=wall_follow_bridge ;;
    esac
    ldd "/opt/chimaera/ros/lib/${package}/${binary}" > /tmp/dependencies.txt
    if grep -q 'not found' /tmp/dependencies.txt; then
        cat /tmp/dependencies.txt
        exit 1
    fi
done
python3 /opt/chimaera/scripts/verify-artifacts.py /opt/chimaera
echo 'Custom m5 symbols and ROS executable dependencies passed'
