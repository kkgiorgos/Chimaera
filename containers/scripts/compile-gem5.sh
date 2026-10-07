#!/usr/bin/env bash
set -eo pipefail
jobs=$1
sdk_key=$(sha256sum /opt/chimaera/packages.tsv)
sdk_key=${sdk_key%% *}
cache="/var/cache/chimaera/${sdk_key}"
mkdir -p "$cache/m5" "$cache/gem5"
# Keep the pinned runtime library at its final RPATH location in every stage.
CXX=g++-12 CC=gcc-12 bash /src/gem5/ext/ramulator2/setup.sh "$jobs"
mkdir -p /opt/chimaera/ramulator2
install -m 755 /src/gem5/ext/ramulator2/ramulator2/libramulator.so /opt/chimaera/ramulator2/
install -m 644 /src/gem5/ext/ramulator2/configs/*.json /opt/chimaera/ramulator2/
install -m 644 /src/gem5/src/mem/ramulator2/LICENSE /opt/chimaera/ramulator2/LICENSE
ln -sfn "$cache/m5" /src/gem5/util/m5/build
(
    cd /src/gem5/util/m5
    scons -j "$jobs" build/x86/out/m5
)
(
    cd /src/gem5
    scons defconfig "$cache/gem5/build/X86" build_opts/X86
    scons -j "$jobs" "$cache/gem5/build/X86/gem5.opt" \
        RAMULATOR2_ROOT=ext/ramulator2/ramulator2 CXX=g++-12 CC=gcc-12 \
        --linker=lld
)
mkdir -p /opt/chimaera/gem5/build/X86 /opt/chimaera/gem5/util/m5/build/x86/out
install -m 755 "$cache/gem5/build/X86/gem5.opt" /opt/chimaera/gem5/build/X86/gem5.opt
install -m 755 "$cache/m5/x86/out/m5" /opt/chimaera/gem5/util/m5/build/x86/out/m5
install -m 644 "$cache/m5/x86/out/libm5.a" /opt/chimaera/gem5/util/m5/build/x86/out/libm5.a
