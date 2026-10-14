"""gem5 config: calibrate the independent device with an idle guest port."""
import argparse
import json
import math
from pathlib import Path

import m5
from m5.objects import (AddrRange, Ramulator2, Root, SrcClockDomain, System,
                        SystemXBar, TrafficGen, VoltageDomain)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', required=True)
parser.add_argument('--pattern', choices=('stream', 'stride', 'random'), required=True)
parser.add_argument('--read-percent', type=int, default=80)
parser.add_argument('--stride', type=int, default=4096)
parser.add_argument('--max-pending', type=int, default=512)
parser.add_argument('--window', type=int, default=268435456)
parser.add_argument('--seed', type=int, default=1)
parser.add_argument('--warmup-ns', type=int, default=100000)
parser.add_argument('--duration-ns', type=int, default=1000000)
parser.add_argument('--offered-gbps', type=float, default=0,
                    help='Optional paced demand for rate validation; zero calibrates the unpaced source')
args = parser.parse_args()
if not math.isfinite(args.offered_gbps) or args.offered_gbps < 0:
    parser.error('offered-gbps must be finite and nonnegative')
m5.ticks.setGlobalFrequency('1THz')
system = System(mem_mode='timing', mem_ranges=[AddrRange('3GiB')])
system.clk_domain = SrcClockDomain(clock='3GHz', voltage_domain=VoltageDomain())
system.membus = SystemXBar()
system.system_port = system.membus.cpu_side_ports
system.memory = Ramulator2(range=system.mem_ranges[0],
    ramulator_config=Path(args.config).read_text(), aggressor_pattern=args.pattern,
    aggressor_interval=1, aggressor_backpressure=True,
    aggressor_gbps=args.offered_gbps,
    aggressor_read_percent=args.read_percent, aggressor_stride=args.stride,
    aggressor_max_pending=args.max_pending, aggressor_window=args.window,
    aggressor_seed=args.seed)
system.memory.port = system.membus.mem_side_ports
idle = Path(m5.options.outdir) / 'idle.cfg'
idle.write_text('STATE 0 1000000000000000 IDLE\nINIT 0\nTRANSITION 0 0 1\n')
system.idle = TrafficGen(config_file=str(idle))
system.idle.port = system.membus.cpu_side_ports
root = Root(full_system=False, system=system)
m5.instantiate()
m5.simulate(args.warmup_ns * 1000)
m5.stats.reset()
m5.simulate(args.duration_ns * 1000)
m5.stats.dump()
