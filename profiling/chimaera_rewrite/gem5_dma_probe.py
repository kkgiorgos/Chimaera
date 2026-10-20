"""Fresh-boot DMA device validation with the deployed detailed CPU geometry."""
import argparse
import json
from pathlib import Path
import sys

import m5
from m5.objects import ChimaeraDma, X86IntelMPIOIntAssignment
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.simulator import Simulator

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime', required=True)
parser.add_argument('--image', required=True)
parser.add_argument('--kernel', required=True)
parser.add_argument('--module', required=True)
parser.add_argument('--probe', required=True)
parser.add_argument('--cpu-type', choices=('timing', 'kvm'), default='timing')
parser.add_argument('--socket-path', required=True)
parser.add_argument('--mailbox-h2g')
parser.add_argument('--mailbox-g2h')
parser.add_argument('--dma-window', type=int, default=64)
parser.add_argument('--direct-dma', action='store_true')
args = parser.parse_args()
if bool(args.mailbox_h2g) != bool(args.mailbox_g2h):
    parser.error('both mailbox paths are required')
if not 1 <= args.dma_window <= 64:
    parser.error('DMA window must be between 1 and 64 cache-line requests')
sys.path.insert(0, str(Path(args.runtime) / 'share/chimaera_ros_bridge/config'))
from chimaera_gem5 import TimingServer, configure_ticks, guest_start_script

configure_ticks()


class ProbeCacheHierarchy(MESITwoLevelCacheHierarchy):
    def incorporate_cache(self, board):
        super().incorporate_cache(board)
        for controller in self._dma_controllers:
            controller.dma_sequencer.max_outstanding_requests = args.dma_window


class ProbeBoard(X86Board):
    def get_dma_ports(self):
        ports = list(super().get_dma_ports())
        if args.direct_dma:
            ports.append(self.chimaera_dma.dma)
        return ports


processor = (SimpleProcessor(cpu_type=CPUTypes.KVM, isa=ISA.X86, num_cores=2)
             if args.cpu_type == 'kvm' else SimpleSwitchableProcessor(
                 starting_core_type=CPUTypes.KVM, switch_core_type=CPUTypes.TIMING,
                 isa=ISA.X86, num_cores=2))
for core in processor.get_cores():
    core.get_simobject().usePerf = False
board = ProbeBoard(clk_freq='3GHz', processor=processor,
                 memory=SingleChannelDDR3_1600(size='3GiB'),
                 cache_hierarchy=ProbeCacheHierarchy(
                     l1d_size='16KiB', l1d_assoc=8, l1i_size='16KiB', l1i_assoc=8,
                     l2_size='256KiB', l2_assoc=16, num_l2_banks=1))
# File injection uses only the transient CowDiskImage. Base images are read-only.
preamble = 'mkdir -p /tmp/chimaera-dma-probe\n'
files = [f'/tmp/chimaera-dma-probe/driver.ko={args.module}',
         f'/tmp/chimaera-dma-probe/probe={args.probe}']
command = (f'chimaera_root /tmp/chimaera-dma-probe/probe {args.cpu_type} '
           '/tmp/chimaera-dma-probe/driver.ko\n')
script = guest_start_script('/bin/true', files, preamble=preamble)
script = script.replace('chimaera_root /bin/true\n', command)
board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=args.kernel),
    disk_image=DiskImageResource(local_path=args.image),
    # gem5's minimal ACPI tables have no PCI _PRT. Select its MP table for
    # interrupt routing, where the endpoint's INTA/GSI mapping is explicit.
    kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923', 'root=/dev/sda2',
                 'mce=off', 'acpi=off'],
    readfile_contents=script)

board.chimaera_dma = ChimaeraDma(pci_dev=5, pci_func=0,
                                upstream=board.pc.pci_host, loopback=not args.mailbox_h2g,
                                h2g_mailbox=args.mailbox_h2g or '/tmp/chimaera_dma_h2g',
                                g2h_mailbox=args.mailbox_g2h or '/tmp/chimaera_dma_g2h')
if args.direct_dma:
    board.pc.pci_bus.mem_side_ports = board.chimaera_dma.pio
else:
    board.pc.attachPciDevice(board.chimaera_dma)
assignment = X86IntelMPIOIntAssignment(
    interrupt_type='INT', polarity='ConformPolarity', trigger='ConformTrigger',
    source_bus_id=0, source_bus_irq=5 << 2,
    dest_io_apic_id=2, dest_io_apic_intin=17)
board.chimaera_mp_irq = assignment
board.workload.intel_mp_table.base_entries = [
    *board.workload.intel_mp_table.base_entries, assignment]


def ready():
    if args.cpu_type != 'kvm':
        processor.switch()
    origin = m5.curTick() // 1000
    Path(str(board.readfile)).write_text(f'CHIMAERA_TIME_ORIGIN_NS {origin}\n')
    (Path(m5.options.outdir) / 'dma_probe_origin.json').write_text(json.dumps(dict(
        origin_ns=origin, cpu=args.cpu_type, cpu_clock='3GHz', num_cores=2,
        dma_window=args.dma_window,
        direct_dma=args.direct_dma,
        active_cpus=[core.get_simobject().type for core in processor.get_cores()])))


class ProbeTimingServer(TimingServer):
    def handle_command(self, line):
        if line == 'CHIMAERA_INGRESS':
            if not self.roi_started or self.finished:
                raise ValueError('ingress requires a live, paused ROI')
            board.chimaera_dma.ingress()
            return 'OK INGRESS'
        return super().handle_command(line)


timing = ProbeTimingServer(on_ready=ready)
simulator = Simulator(board=board, on_exit_event=timing.exit_handlers())
timing.run(simulator, args.socket_path)
