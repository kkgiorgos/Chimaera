"""Exercise atomic backing-store access followed by timing execution/drain."""

import argparse
from pathlib import Path

import m5
from m5.objects import (
    AddrRange,
    AtomicSimpleCPU,
    Process,
    Ramulator2,
    Root,
    SEWorkload,
    SrcClockDomain,
    System,
    SystemXBar,
    TimingSimpleCPU,
    VoltageDomain,
)

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--binary", required=True)
args = parser.parse_args()
binary = str(Path(args.binary).resolve())
system = System(mem_mode="atomic", mem_ranges=[AddrRange("512MiB")])
system.clk_domain = SrcClockDomain(
    clock="3GHz", voltage_domain=VoltageDomain()
)
system.membus = SystemXBar()
system.system_port = system.membus.cpu_side_ports
system.memory = Ramulator2(
    range=system.mem_ranges[0],
    ramulator_config=Path(args.config).read_text(),
    max_outstanding=2,
)
system.memory.port = system.membus.mem_side_ports
system.cpu = AtomicSimpleCPU(cpu_id=0)
system.switched_cpu = TimingSimpleCPU(cpu_id=0, switched_out=True)
system.workload = SEWorkload.init_compatible(binary)
process = Process(cmd=[binary])
for cpu in (system.cpu, system.switched_cpu):
    cpu.workload = process
    cpu.createThreads()
system.cpu.createInterruptController()
system.cpu.connectBus(system.membus)
root = Root(full_system=False, system=system)
m5.instantiate()

event = m5.simulate(10_000)
assert event.getCause() == "simulate() limit reached", event.getCause()
m5.stats.dump()
# During atomic execution the DRAM clock and request counters remain zero.
atomic_stats = Path(m5.options.outdir) / (
    f"system.memory.ramulator_stats.{m5.curTick()}.yaml"
)
text = atomic_stats.read_text()
assert "total_num_read_requests: 0" in text, text
m5.switchCpus(system, [(system.cpu, system.switched_cpu)])
m5.stats.reset()
event = m5.simulate(100_000_000_000)
assert event.getCause() == "exiting with last active thread context", (
    event.getCause()
)
m5.drain()
m5.stats.dump()
print("RAMULATOR_MODE_SWITCH_OK")
