"""Full-system ball catching: KVM boot followed by a fixed Timing CPU ROI."""
import argparse
import json
from pathlib import Path
import re

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.ramulator2 import Ramulator2Memory
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.simulator import Simulator

from chimaera_gem5 import TimingServer, add_chimaera_arguments, configure_ticks, guest_start_script

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5-root', required=True)
parser.add_argument('--experiment', required=True)
parser.add_argument('--bundle', required=True)
parser.add_argument('--image', required=True)
parser.add_argument('--kernel', required=True)
add_chimaera_arguments(parser, guest_command='/opt/chimaera/ball_catching/guest_start')
args = parser.parse_args()
experiment = json.loads(Path(args.experiment).read_text())
hardware = experiment['hardware']
configure_ticks()
processor = SimpleSwitchableProcessor(starting_core_type=CPUTypes.KVM,
    switch_core_type=CPUTypes.TIMING, isa=ISA.X86, num_cores=hardware['num_cores'])
for core in processor.get_cores():
    core.get_simobject().usePerf = False
cache = MESITwoLevelCacheHierarchy(l1d_size=hardware['l1d_size'],
    l1i_size=hardware['l1i_size'], l1d_assoc=hardware['l1_assoc'],
    l1i_assoc=hardware['l1_assoc'], l2_size=hardware['l2_size'],
    l2_assoc=hardware['l2_assoc'], num_l2_banks=1)
aggressor = {'aggressor_' + key: value for key, value in experiment['aggressor'].items()}
memory = Ramulator2Memory(experiment['ramulator_config'], size='3GiB', aggressor=aggressor)
board = X86Board(clk_freq=hardware['cpu_clock'], processor=processor,
                 memory=memory, cache_hierarchy=cache)
# The board uses a gem5 COW disk overlay. Stage into the guest at KVM boot;
# the user's base image is never mounted or modified.
preamble = 'chimaera_root mkdir -p /opt/chimaera\n'
files = ['/tmp/chimaera-ball.tar.gz=' + args.bundle, *args.guest_file]
boot = guest_start_script('/tmp/chimaera-ball-start', files, preamble=preamble)
board.set_kernel_disk_workload(kernel=KernelResource(local_path=args.kernel),
    disk_image=DiskImageResource(local_path=args.image),
    kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923', 'root=/dev/sda2', 'mce=off'],
    readfile_contents=boot)

def prepare_roi():
    serial = (Path(m5.options.outdir) / 'board.pc.com_1.device').read_text(errors='replace')
    serial = re.sub(r'\[\s*\d+\.\d+\] [^\r\n]*(?:\r?\n|$)', '', serial)
    counts = re.findall(r'^CHIMAERA_ONLINE_CPUS=(\d+)\r?$', serial, re.MULTILINE)
    if not counts or int(counts[-1]) != hardware['num_cores']:
        raise RuntimeError('Guest CPU count differs from the fixed architecture; inspect serial log')
    processor.switch()

class BallTimingServer(TimingServer):
    def _run_once(self):
        event = super()._run_once()
        if not self.roi_started:
            serial = (Path(m5.options.outdir) / 'board.pc.com_1.device').read_text(errors='replace')
            if 'CHIMAERA_GUEST_APPLICATION_EXIT=' in serial:
                raise RuntimeError('Guest application exited before workbegin; inspect serial log')
        return event

    def handle_command(self, line):
        if line == 'BENCHMARK_RESET':
            m5.stats.reset()
            (Path(m5.options.outdir) / 'measurement_start.json').write_text(
                json.dumps({'tick': m5.curTick(), 'frequency': 1e12}) + '\n')
            return f'RESET {m5.curTick()}'
        if line == 'BENCHMARK_DUMP':
            m5.stats.dump()
            return f'DUMP {m5.curTick()}'
        return super().handle_command(line)

timing = BallTimingServer(on_ready=prepare_roi)
simulator = Simulator(board=board, on_exit_event=timing.exit_handlers())
timing.run(simulator, args.socket_path, managed_shutdown=args.managed_shutdown)
