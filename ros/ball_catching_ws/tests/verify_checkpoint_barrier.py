"""Verify the guest restore gate through a real gem5 drain/save/restore.

Build checkpoint_barrier.cc statically against libm5, m5_mmap.c and the bridge includes.
Run this config once to capture, then with --restore, using the same arguments.
"""
import argparse
from pathlib import Path

import m5
from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.ramulator2 import Ramulator2Memory
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--memory', type=Path, required=True)
parser.add_argument('--checkpoint', type=Path, required=True)
parser.add_argument('--restore', action='store_true')
args = parser.parse_args()
m5.ticks.setGlobalFrequency('1THz')
cache = MESITwoLevelCacheHierarchy(l1d_size='32KiB', l1i_size='32KiB', l1d_assoc=8,
    l1i_assoc=8, l2_size='8MiB', l2_assoc=16, num_l2_banks=1,
    functional_checkpoint_writeback=True)
board = SimpleBoard(clk_freq='3GHz', cache_hierarchy=cache,
    memory=Ramulator2Memory(str(args.memory), size='64MiB',
        aggressor=dict(aggressor_pattern='stream', aggressor_enabled=False)),
    processor=SimpleProcessor(cpu_type=CPUTypes.TIMING, isa=ISA.X86, num_cores=1))
control = args.checkpoint.parent / 'checkpoint-control'
control.write_text('CHIMAERA_TIME_ORIGIN_NS 0\n' if args.restore else 'CHIMAERA_CHECKPOINT\n')
board.readfile = str(control)
board.set_se_binary_workload(BinaryResource(local_path=str(args.binary)),
    checkpoint=args.checkpoint if args.restore else None)
simulator = Simulator(board=board, max_ticks=1_000_000_000,
    on_exit_event={ExitEvent.CHECKPOINT: lambda: True})
simulator.run()
if args.restore:
    if (ExitEvent.translate_exit_status(simulator.get_last_exit_event_cause()) != ExitEvent.EXIT
            or simulator.get_last_exit_event_code() != 0):
        raise RuntimeError('Restored checkpoint barrier did not release')
    print('CHECKPOINT_BARRIER_VERIFIED', flush=True)
else:
    if simulator.get_last_exit_event_cause() != 'checkpoint':
        raise RuntimeError('Workload did not reach its checkpoint')
    simulator.save_checkpoint(args.checkpoint)
    print('CHECKPOINT_BARRIER_SAVED', flush=True)
