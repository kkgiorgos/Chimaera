"""Run the ordering probe in SE mode, without Linux, ROS, or Gazebo."""
import argparse

import m5
from m5.objects import (
    AddrRange,
    Cache,
    Process,
    Root,
    SEWorkload,
    SimpleMemory,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
    X86O3CPU,
    X86TimingSimpleCPU,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True)
parser.add_argument("--cpu", choices=("timing", "o3"), required=True)
parser.add_argument("--cache", choices=("classic", "ruby"), required=True)
parser.add_argument("--readfile", required=True)
args = parser.parse_args()

if args.cache == "classic":
    system = System()
    system.clk_domain = SrcClockDomain(
        clock="3GHz", voltage_domain=VoltageDomain()
    )
    system.mem_mode = "timing"
    system.mem_ranges = [AddrRange("64MiB")]
    system.cpu = X86O3CPU() if args.cpu == "o3" else X86TimingSimpleCPU()
    system.bus = SystemXBar()
    for name in ("icache", "dcache"):
        cache = Cache(
            size="32KiB",
            assoc=8,
            tag_latency=1,
            data_latency=1,
            response_latency=1,
            mshrs=8,
            tgts_per_mshr=16,
        )
        setattr(system, name, cache)
        setattr(system.cpu, name + "_port", cache.cpu_side)
        cache.mem_side = system.bus.cpu_side_ports
    system.cpu.createInterruptController()
    system.cpu.interrupts[0].pio = system.bus.mem_side_ports
    system.cpu.interrupts[0].int_requestor = system.bus.cpu_side_ports
    system.cpu.interrupts[0].int_responder = system.bus.mem_side_ports
    system.memory = SimpleMemory(range=system.mem_ranges[0], latency="50ns")
    system.memory.port = system.bus.mem_side_ports
    system.system_port = system.bus.cpu_side_ports
    system.workload = SEWorkload.init_compatible(args.binary)
    system.cpu.workload = Process(cmd=[args.binary])
    system.cpu.createThreads()
    system.readfile = args.readfile
    root = Root(full_system=False, system=system)
    m5.instantiate()
    event = m5.simulate(100_000_000)
    cause, code = event.getCause(), event.getCode()
else:
    from gem5.components.boards.simple_board import SimpleBoard
    from gem5.components.cachehierarchies.ruby import (
        mesi_two_level_cache_hierarchy as mesi,
    )
    from gem5.components.memory import SingleChannelDDR3_1600
    from gem5.components.processors.cpu_types import CPUTypes
    from gem5.components.processors.simple_processor import SimpleProcessor
    from gem5.isas import ISA
    from gem5.resources.resource import BinaryResource
    from gem5.simulate.simulator import Simulator

    board = SimpleBoard(
        clk_freq="3GHz",
        processor=SimpleProcessor(
            cpu_type=CPUTypes.O3 if args.cpu == "o3" else CPUTypes.TIMING,
            isa=ISA.X86,
            num_cores=1,
        ),
        memory=SingleChannelDDR3_1600(size="64MiB"),
        cache_hierarchy=mesi.MESITwoLevelCacheHierarchy(
            l1i_size="32KiB",
            l1i_assoc=8,
            l1d_size="32KiB",
            l1d_assoc=8,
            l2_size="256KiB",
            l2_assoc=16,
            num_l2_banks=1,
        ),
    )
    board.readfile = args.readfile
    board.set_se_binary_workload(BinaryResource(local_path=args.binary))
    simulator = Simulator(board=board)
    simulator.run(max_ticks=100_000_000)
    cause = simulator.get_last_exit_event_cause()
    code = simulator.get_last_exit_event_code()

print(f"Probe exit: {cause}; code: {code}; tick: {m5.curTick()}")
if cause != "exiting with last active thread context":
    raise RuntimeError("Probe did not exit normally")
raise SystemExit(code)
