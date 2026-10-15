"""Short process-attribution probe; no ROS, Gazebo, or robot experiment."""

import argparse
import base64
from pathlib import Path

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby import (
    mesi_two_level_cache_hierarchy,
)
from gem5.components.memory.ramulator2 import Ramulator2Memory
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import (
    SimpleSwitchableProcessor,
)
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", required=True)
parser.add_argument("--kernel", required=True)
parser.add_argument("--image", required=True)
parser.add_argument('--probe', required=True)
parser.add_argument('--aggressor-gbps', type=float, default=0)
args = parser.parse_args()
payload = base64.b64encode(Path(args.probe).read_bytes()).decode('ascii')
processor = SimpleSwitchableProcessor(
    starting_core_type=CPUTypes.KVM,
    switch_core_type=CPUTypes.TIMING,
    isa=ISA.X86,
    num_cores=2,
)
for core in processor.get_cores():
    core.get_simobject().usePerf = False
board = X86Board(
    clk_freq="3GHz",
    processor=processor,
    memory=Ramulator2Memory(args.config, size="3GiB", attribute_nodes=True,
        aggressor=dict(aggressor_pattern='stride' if args.aggressor_gbps else 'none',
                       aggressor_gbps=args.aggressor_gbps,
                       aggressor_backpressure=True, aggressor_max_pending=512)),
    cache_hierarchy=mesi_two_level_cache_hierarchy.MESITwoLevelCacheHierarchy(
        l1d_size="16KiB",
        l1d_assoc=8,
        l1i_size="16KiB",
        l1i_assoc=8,
        l2_size="64KiB",
        l2_assoc=16,
        num_l2_banks=1,
    ),
)
board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=args.kernel),
    disk_image=DiskImageResource(local_path=args.image),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=/dev/sda2",
        "mce=off",
    ],
    readfile_contents="""#!/bin/bash
set -eu
m5_binary="$(command -v m5)"
printf %s """ + payload + """ | base64 -d > /tmp/node-dram-probe
chmod +x /tmp/node-dram-probe
"$m5_binary" --addr workbegin 0 0
/tmp/node-dram-probe
printf 'RAMULATOR_FS_GUEST_OK\\n'
"$m5_binary" --addr workend 0 0
"$m5_binary" --addr exit
""",
)
switched = False
finished = False


def begin():
    global switched
    processor.switch()
    m5.stats.reset()
    switched = True
    yield False


def end():
    global finished
    finished = True
    yield True


Simulator(
    board=board,
    on_exit_event={ExitEvent.WORKBEGIN: begin(), ExitEvent.WORKEND: end()},
).run()
assert switched and finished, "Guest did not complete both ROI markers"
m5.drain()
m5.stats.dump()
print("NODE_DRAM_FULL_SYSTEM_OK")
