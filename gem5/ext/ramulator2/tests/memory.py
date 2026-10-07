"""Data correctness, retries, vector routing, and stats without a guest."""

import argparse
import copy
import json
from pathlib import Path

import m5
from m5.objects import (
    AddrRange,
    MemTest,
    Ramulator2,
    Ramulator2VectorPorts,
    Root,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
)

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--vector", action="store_true")
parser.add_argument("--aggressor", choices=("stream", "stride", "random"))
args = parser.parse_args()
config = json.loads(Path(args.config).read_text())
system = System(mem_mode="timing", mem_ranges=[AddrRange("32MiB")])
system.clk_domain = SrcClockDomain(
    clock="3GHz", voltage_domain=VoltageDomain()
)
system.membus = SystemXBar()
system.system_port = system.membus.cpu_side_ports

if args.vector:
    ms = config["memory_system"]
    ms["controllers"].append(copy.deepcopy(ms["controllers"][0]))
    ms["channel_mapper"] = {
        "impl": "Gem5PortInterleave",
        "interleave_size": 64,
    }
    system.memory = Ramulator2VectorPorts(
        range=system.mem_ranges[0],
        ramulator_config=json.dumps(config),
        max_outstanding=2,
        port_ranges=[
            AddrRange(
                start=0, size="32MiB", intlvHighBit=6,
                intlvBits=1, intlvMatch=i
            )
            for i in range(2)
        ],
    )
    for i in range(2):
        system.memory.ports[i] = system.membus.mem_side_ports
else:
    system.memory = Ramulator2(
        range=system.mem_ranges[0],
        ramulator_config=json.dumps(config),
        max_outstanding=2,
        aggressor_pattern=args.aggressor or "none",
        aggressor_interval=1,
        aggressor_max_pending=2,
    )
    system.memory.port = system.membus.mem_side_ports

# More requestors than pending slots forces request retries. MemTest compares
# every read to independently maintained reference data and mixes functional
# accesses with small timing writes, including write forwarding/coalescing.
system.testers = [
    MemTest(
        max_loads=2000,
        size=512,
        percent_reads=65,
        percent_functional=10,
        percent_uncacheable=0,
    )
    for _ in range(4)
]
for tester in system.testers:
    tester.port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)
m5.instantiate()
first = m5.simulate(100_000_000)
assert first.getCause() == "simulate() limit reached", first.getCause()
m5.stats.dump()
m5.stats.reset()
second = m5.simulate(1_000_000_000)
assert second.getCause() == "maximum number of loads reached", (
    second.getCause()
)
m5.stats.dump()
print("RAMULATOR_MEMORY_OK")
