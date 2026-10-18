"""Linux/KVM-to-detailed checkpoint gate regression using address-mode m5ops.

Build checkpoint_barrier.cc statically with libm5 and m5_mmap.c. Capture requires
KVM and four virtual CPUs; restore uses detailed CPUs only. --quiesce reproduces
the old address-mode gate. All writes use gem5's COW disk overlay and the outdir.
"""
import argparse
import base64
import json
from pathlib import Path

import m5
from gem5.components.boards.mem_mode import MemMode
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.ramulator2 import Ramulator2Memory
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_core import SimpleCore
from gem5.components.processors.switchable_processor import SwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--memory', type=Path, required=True)
parser.add_argument('--image', type=Path, required=True)
parser.add_argument('--kernel', type=Path, required=True)
parser.add_argument('--checkpoint', type=Path, required=True)
parser.add_argument('--restore', action='store_true')
parser.add_argument('--quiesce', action='store_true')
args = parser.parse_args()
m5.ticks.setGlobalFrequency('1THz')
cores = {'detailed': [SimpleCore(cpu_type=CPUTypes.TIMING, isa=ISA.X86, core_id=i)
                       for i in range(4)]}
if not args.restore:
    cores['boot'] = [SimpleCore(cpu_type=CPUTypes.KVM, isa=ISA.X86, core_id=i) for i in range(4)]
    for core in cores['boot']:
        core.get_simobject().usePerf = False
processor = SwitchableProcessor(switchable_cores=cores,
    starting_cores='detailed' if args.restore else 'boot')
board = X86Board(clk_freq='3GHz', processor=processor,
    cache_hierarchy=MESITwoLevelCacheHierarchy(l1d_size='32KiB', l1i_size='32KiB',
        l1d_assoc=8, l1i_assoc=8, l2_size='8MiB', l2_assoc=16, num_l2_banks=1,
        functional_checkpoint_writeback=True),
    memory=Ramulator2Memory(str(args.memory), size='3GiB',
        aggressor=dict(aggressor_pattern='stream', aggressor_enabled=False)))
board.set_mem_mode(MemMode.TIMING if args.restore else MemMode.ATOMIC_NONCACHING)
payload = base64.b64encode(args.binary.read_bytes()).decode('ascii')
boot = (f'#!/bin/bash\nset -e\nprintf %s {payload} | base64 -d > /tmp/checkpoint-barrier\n'
        'chmod +x /tmp/checkpoint-barrier\nexec /tmp/checkpoint-barrier --address'
        + (' --quiesce' if args.quiesce else '') + '\n')
board.set_kernel_disk_workload(kernel=KernelResource(local_path=str(args.kernel)),
    disk_image=DiskImageResource(local_path=str(args.image)), readfile_contents=boot,
    kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923', 'root=/dev/sda2', 'mce=off'],
    checkpoint=args.checkpoint if args.restore else None)

def workbegin():
    processor.switch_to_processor('detailed')
    Path(str(board.readfile)).write_text('CHIMAERA_CHECKPOINT\n')
    yield False

simulator = Simulator(board=board, max_ticks=300_000_000_000_000,
    on_exit_event={ExitEvent.WORKBEGIN: workbegin(), ExitEvent.CHECKPOINT: lambda: True,
                  ExitEvent.WORKEND: lambda: True})
if args.restore:
    simulator._instantiate()
    saved = json.loads((args.checkpoint.parent / 'saved-tick.json').read_text())['tick']
    m5.simulate(0)
    assert m5.curTick() == saved
    Path(str(board.readfile)).write_text('CHIMAERA_TIME_ORIGIN_NS 0\n')
simulator.run()
if args.restore:
    result = Path(m5.options.outdir) / 'checkpoint-gate-restored.txt'
    if (not result.is_file() or 'CHECKPOINT_BARRIER_RESTORE_OK' not in result.read_text()
            or simulator.get_last_exit_event_code() != 0):
        raise RuntimeError('Linux guest did not pass the restored gate')
    print('CHECKPOINT_BARRIER_FS_RESTORED', flush=True)
else:
    if simulator.get_last_exit_event_cause() != 'checkpoint':
        raise RuntimeError('Linux guest did not reach its checkpoint')
    # Require actual repeated polling before draining. A short fixed tick
    # budget can save before the guest has reached the problematic operation.
    simulator.run()
    if ExitEvent.translate_exit_status(simulator.get_last_exit_event_cause()) != ExitEvent.WORKEND:
        raise RuntimeError('Linux guest did not exercise the checkpoint wait loop: '
                           + simulator.get_last_exit_event_cause())
    print('CHECKPOINT_BARRIER_FS_GATE_ACTIVE', flush=True)
    output = Path(m5.options.outdir)
    if (output / 'checkpoint-gate-control.txt').read_text() != 'CHIMAERA_CHECKPOINT\n':
        raise RuntimeError('Guest did not observe the capture marker')
    if (output / 'checkpoint-gate-restored.txt').exists():
        raise RuntimeError('Guest escaped the capture gate before restore')
    m5.checkpoint(str(args.checkpoint))
    (args.checkpoint.parent / 'saved-tick.json').write_text(json.dumps({'tick': m5.curTick()}))
    print('CHECKPOINT_BARRIER_FS_SAVED', flush=True)
