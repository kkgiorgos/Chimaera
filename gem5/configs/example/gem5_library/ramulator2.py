"""Run a local X86 SE binary with Ramulator 2.1 (no guest image needed)."""

import argparse
from pathlib import Path

from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.classic.no_cache import NoCache
from gem5.components.memory.ramulator2 import Ramulator2Memory
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.simulator import Simulator

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True)
parser.add_argument("--config", required=True)
parser.add_argument("--size", default="512MiB")
parser.add_argument("--cpu-type", choices=("timing", "o3"), default="timing")
args = parser.parse_args()

board = SimpleBoard(
    clk_freq="3GHz",
    processor=SimpleProcessor(
        cpu_type={"timing": CPUTypes.TIMING, "o3": CPUTypes.O3}[args.cpu_type],
        isa=ISA.X86,
        num_cores=1,
    ),
    memory=Ramulator2Memory(args.config, size=args.size),
    cache_hierarchy=NoCache(),
)
board.set_se_binary_workload(
    binary=BinaryResource(local_path=str(Path(args.binary).resolve()))
)
Simulator(board=board).run()
