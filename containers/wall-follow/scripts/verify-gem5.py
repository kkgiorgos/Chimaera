"""Run through gem5's embedded Python to check the required build features."""

import json
from pathlib import Path

from m5 import core
from m5.defines import buildEnv
from m5.objects import X86KvmCPU, X86O3CPU, X86TimingSimpleCPU

assert buildEnv["USE_X86_ISA"], "Missing x86 support"
assert buildEnv["USE_KVM"], "Missing KVM support"
assert buildEnv["RUBY_PROTOCOL_MESI_Two_Level"], "Missing MESI_Two_Level protocol"
profile = json.loads(Path("/opt/chimaera/profile.json").read_text())
assert core.gem5Version == profile["gem5"]["version"], "gem5 version differs from the stack profile"
print(json.dumps({
    "gem5_version": core.gem5Version,
    "x86": True,
    "kvm": X86KvmCPU.__name__,
    "timing": X86TimingSimpleCPU.__name__,
    "o3": X86O3CPU.__name__,
    "protocol": "MESI_Two_Level",
}))
