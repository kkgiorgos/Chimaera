#!/usr/bin/env bash
set -eo pipefail
jobs=$1
sdk_key=$(sha256sum /opt/chimaera/packages.tsv)
sdk_key=${sdk_key%% *}
cache="/var/cache/chimaera/${sdk_key}"
mkdir -p "$cache/m5" "$cache/gem5"
ln -sfn "$cache/m5" /src/gem5/util/m5/build
(
    cd /src/gem5/util/m5
    scons -j "$jobs" build/x86/out/m5
)
(
    cd /src/gem5
    scons defconfig "$cache/gem5/build/X86" build_opts/X86
    scons -j "$jobs" "$cache/gem5/build/X86/gem5.opt"
)
mkdir -p /opt/chimaera/gem5/build/X86 /opt/chimaera/gem5/util/m5/build/x86/out
install -m 755 "$cache/gem5/build/X86/gem5.opt" /opt/chimaera/gem5/build/X86/gem5.opt
install -m 755 "$cache/m5/x86/out/m5" /opt/chimaera/gem5/util/m5/build/x86/out/m5
install -m 644 "$cache/m5/x86/out/libm5.a" /opt/chimaera/gem5/util/m5/build/x86/out/libm5.a
