"""gem5 config: save/restore dirty MESI/Ramulator memory without a Linux boot.

Build checkpoint_memory.cc statically with libm5; run this config first without
--restore and then with it, using the same --checkpoint, --binary and --memory.
"""
import argparse
import configparser
import gzip
from pathlib import Path
import struct
import subprocess

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
memory = Ramulator2Memory(str(args.memory), size='64MiB',
    aggressor=dict(aggressor_pattern='stream', aggressor_enabled=False))
board = SimpleBoard(clk_freq='3GHz', cache_hierarchy=cache, memory=memory,
    processor=SimpleProcessor(cpu_type=CPUTypes.TIMING, isa=ISA.X86, num_cores=1))
board.set_se_binary_workload(BinaryResource(local_path=str(args.binary)),
    checkpoint=args.checkpoint if args.restore else None)
simulator = Simulator(board=board, max_ticks=1_000_000_000_000,
    on_exit_event={ExitEvent.CHECKPOINT: lambda: True})
if args.restore:
    simulator._instantiate()
    tick = m5.curTick()
    m5.simulate(0)
    if m5.curTick() != tick:
        raise RuntimeError('Cache reconstruction changed the restored simulation tick')
    print('Restored saved memory with cold caches at tick', tick, flush=True)
    simulator.run()
    if (ExitEvent.translate_exit_status(simulator.get_last_exit_event_cause()) != ExitEvent.EXIT
            or simulator.get_last_exit_event_code() != 0):
        raise RuntimeError('Restored memory workload failed')
    print('CHECKPOINT_MEMORY_RESTORE_OK', flush=True)
else:
    simulator.run()
    if simulator.get_last_exit_event_cause() != 'checkpoint':
        raise RuntimeError('Workload did not reach its checkpoint')
    simulator.save_checkpoint(args.checkpoint)
    # Check backing RAM independently of Ruby's cache trace: otherwise a warm
    # restored cache could hide missing dirty bytes in the physical snapshot.
    symbols = subprocess.check_output(['nm', '-n', '-C', str(args.binary)], text=True)
    address = next(int(line.split()[0], 16) for line in symbols.splitlines()
                   if len(line.split()) == 3 and line.split()[2] == 'values')
    state = configparser.ConfigParser(interpolation=None)
    state.read(args.checkpoint / 'm5.cpt')
    if int(state['board.cache_hierarchy.ruby_system']['cache_trace_size']) != 0:
        raise RuntimeError('Functional checkpoints must restore with cold caches')
    pages = {int(state[section]['vaddr'], 0): int(state[section]['paddr'], 0)
             for section in state.sections() if '.ptable.Entry' in section}
    with gzip.open(args.checkpoint / 'board.physmem.store0.pmem', 'rb') as stream:
        saved_ram = stream.read()
    for index in range(131072):
        virtual = address + index * 8
        physical = pages[virtual & ~4095] + (virtual & 4095)
        value = struct.unpack_from('<Q', saved_ram, physical)[0]
        expected = ((index * 0x9e3779b97f4a7c15) & ((1 << 64) - 1)) ^ 0x123456789abcdef0
        if value != expected:
            raise RuntimeError('Dirty data missing from physical checkpoint RAM at element ' + str(index))
    print('CHECKPOINT_RAM_BYTES_OK', flush=True)
    print('Saved dirty cache and RAM state at tick', m5.curTick(), flush=True)
