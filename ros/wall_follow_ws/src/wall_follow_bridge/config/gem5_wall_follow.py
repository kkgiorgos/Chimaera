# Copyright (c) 2021-2025 The Regents of the University of California
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Wall-follow experiment: KVM boot, configurable CPU/cache ROI, and robot settings.

Imports the universal Chimaera timing integration via gem5's -p MODULE_DIRECTORY.
"""

import argparse
import re
from pathlib import Path

import m5
import m5.ticks
from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

from chimaera_gem5 import TimingServer, add_chimaera_arguments, configure_ticks, guest_start_script

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--gem5-root", required=True)
parser.add_argument("--cpu-type", choices=("kvm", "timing", "o3"), default="timing")
parser.add_argument("--cpu-clock", default="3GHz")
parser.add_argument("--num-cores", type=int, default=2)
parser.add_argument("--l1d-size", default="16KiB")
parser.add_argument("--l1i-size", default="16KiB")
parser.add_argument("--l2-size", default="256KiB")
parser.add_argument("--l1-assoc", type=int, default=8)
parser.add_argument("--l2-assoc", type=int, default=16)
parser.add_argument("--image")
parser.add_argument("--kernel")
parser.add_argument("--controller-file", help="Inject this run's controller YAML into the guest")
add_chimaera_arguments(parser, guest_command="/opt/chimaera/wall_follow/guest_start")
args = parser.parse_args()
if args.num_cores < 1 or args.l1_assoc < 1 or args.l2_assoc < 1:
    parser.error("core count and cache associativity must be positive")
files = list(args.guest_file)
if args.controller_file:
    files.append("/tmp/chimaera-controller.yaml=" + args.controller_file)
boot_script = guest_start_script(
    args.guest_command, files,
    preamble='printf "CHIMAERA_ONLINE_CPUS=%s\\n" "$(getconf _NPROCESSORS_ONLN)"\n')
configure_ticks()

requires(
    coherence_protocol_required=CoherenceProtocol.MESI_TWO_LEVEL,
    kvm_required=True,
)


cache_hierarchy = MESITwoLevelCacheHierarchy(
    l1d_size=args.l1d_size,
    l1d_assoc=args.l1_assoc,
    l1i_size=args.l1i_size,
    l1i_assoc=args.l1_assoc,
    l2_size=args.l2_size,
    l2_assoc=args.l2_assoc,
    num_l2_banks=1,
)

memory = SingleChannelDDR3_1600(size="3GiB")

processor = SimpleProcessor(cpu_type=CPUTypes.KVM, isa=ISA.X86, num_cores=args.num_cores) if args.cpu_type == "kvm" else SimpleSwitchableProcessor(
    starting_core_type=CPUTypes.KVM,
    switch_core_type={"timing": CPUTypes.TIMING, "o3": CPUTypes.O3}[args.cpu_type],
    isa=ISA.X86,
    num_cores=args.num_cores,
)

for core in processor.get_cores():
    core.get_simobject().usePerf = False

board = X86Board(
    clk_freq=args.cpu_clock,
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

resources = Path(args.gem5_root).resolve() / "resources"
board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=str(Path(args.kernel) if args.kernel else resources / "x86-linux-kernel-5.15.180")),
    disk_image=DiskImageResource(local_path=str(Path(args.image) if args.image else resources / "x86-ubuntu-22.04-ros-humble.img")),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=/dev/sda2",
        "mce=off",
    ],
    readfile_contents=boot_script,
)

def verify_online_cpus():
    # Kernel printk messages can interrupt the boot script's marker mid-write.
    # Remove their timestamped lines, including the inserted newline, before
    # checking the guest count and switching CPUs for the measured ROI.
    serial = Path(m5.options.outdir) / "board.pc.com_1.device"
    text = serial.read_text(errors="replace")
    text = re.sub(r"\[\s*\d+\.\d+\] [^\r\n]*(?:\r?\n|$)", "", text)
    counts = re.findall(r"^CHIMAERA_ONLINE_CPUS=(\d+)\r?$", text, re.MULTILINE)
    if not counts:
        raise RuntimeError(f"Guest online CPU count missing from {serial}")
    online = int(counts[-1])
    if online != args.num_cores:
        raise RuntimeError(f"Guest has {online} online CPUs; requested {args.num_cores}. "
                           f"Check the SMP boot errors in {serial}")
    print(f"[host] Verified {online} guest CPUs online", flush=True)


def prepare_roi():
    verify_online_cpus()
    if args.cpu_type != "kvm":
        processor.switch()


timing = TimingServer(on_ready=prepare_roi)
simulator = Simulator(board=board, on_exit_event=timing.exit_handlers())
timing.run(simulator, args.socket_path, managed_shutdown=args.managed_shutdown)
