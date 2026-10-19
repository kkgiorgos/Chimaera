"""Synthetic latency experiment with KVM boot and configurable x86 ROI CPUs."""
import argparse
import base64
import json
from pathlib import Path

import m5
from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

from chimaera_gem5 import TimingServer, add_chimaera_arguments, configure_ticks, guest_start_script

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5-root', required=True)
parser.add_argument('--image', required=True)
parser.add_argument('--kernel', required=True)
parser.add_argument('--guest-bundle')
parser.add_argument('--cpu-type', choices=('kvm', 'timing', 'o3'), default='kvm')
parser.add_argument('--cpu-clock', default='3GHz')
parser.add_argument('--num-cores', type=int, default=2)
parser.add_argument('--l1d-size', default='16KiB')
parser.add_argument('--l1i-size', default='16KiB')
parser.add_argument('--l2-size', default='256KiB')
parser.add_argument('--l1-assoc', type=int, default=8)
parser.add_argument('--l2-assoc', type=int, default=16)
add_chimaera_arguments(parser, guest_command='/opt/chimaera/latency/guest_start')
args = parser.parse_args()
configure_ticks()
requires(coherence_protocol_required=CoherenceProtocol.MESI_TWO_LEVEL, kvm_required=True)
if args.num_cores < 1 or args.l1_assoc < 1 or args.l2_assoc < 1:
    parser.error('Core counts and cache associativity must be positive')
processor = (SimpleProcessor(cpu_type=CPUTypes.KVM, isa=ISA.X86, num_cores=args.num_cores)
    if args.cpu_type == 'kvm' else SimpleSwitchableProcessor(
        starting_core_type=CPUTypes.KVM,
        switch_core_type={'timing': CPUTypes.TIMING, 'o3': CPUTypes.O3}[args.cpu_type],
        isa=ISA.X86, num_cores=args.num_cores))
for core in processor.get_cores():
    core.get_simobject().usePerf = False
board = X86Board(clk_freq=args.cpu_clock, processor=processor,
    memory=SingleChannelDDR3_1600(size='3GiB'),
    cache_hierarchy=MESITwoLevelCacheHierarchy(l1d_size=args.l1d_size, l1d_assoc=args.l1_assoc,
        l1i_size=args.l1i_size, l1i_assoc=args.l1_assoc, l2_size=args.l2_size,
        l2_assoc=args.l2_assoc, num_l2_banks=1))
preamble = 'rm -f /tmp/chimaera-sim-time-origin.ns\n'
if args.guest_bundle:
    encoded = base64.b64encode(Path(args.guest_bundle).read_bytes()).decode('ascii')
    # Extraction runs inside the guest's ephemeral CowDiskImage. No host image
    # mount, sudo, or deployment is required for this mode.
    preamble += f'printf %s {encoded} | base64 -d | chimaera_root tar -xz -C /\n'
board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=args.kernel),
    disk_image=DiskImageResource(local_path=args.image),
    kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923', 'root=/dev/sda2', 'mce=off'],
    readfile_contents=guest_start_script(args.guest_command, args.guest_file,
        preamble=preamble))


def prepare_clock():
    if args.cpu_type != 'kvm':
        processor.switch()
    origin = m5.curTick() // 1000
    Path(str(board.readfile)).write_text(f'CHIMAERA_TIME_ORIGIN_NS {origin}\n')
    (Path(m5.options.outdir) / 'latency_clock_origin.json').write_text(
        json.dumps(dict(origin_ns=origin, cpu=args.cpu_type, num_cores=args.num_cores,
                        cpu_clock=args.cpu_clock,
                        active_cpus=[core.get_simobject().type for core in processor.get_cores()])) + '\n')


timing = TimingServer(on_ready=prepare_clock)
simulator = Simulator(board=board, on_exit_event=timing.exit_handlers())
timing.run(simulator, args.socket_path, managed_shutdown=args.managed_shutdown)
