"""Check stdlib capacity/range validation inside the actual gem5 runtime."""

import argparse

from m5.params import AddrRange

from gem5.components.memory.ramulator2 import Ramulator2Memory

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
args = parser.parse_args()

memory = Ramulator2Memory(args.config, size="3GiB")
for ranges in (
    [],
    [AddrRange("2GiB")],
    [AddrRange("3GiB"), AddrRange("1GiB")],
    [AddrRange(start=0, size="6GiB", intlvHighBit=6,
               intlvBits=1, intlvMatch=0)],
):
    try:
        memory.set_memory_range(ranges)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Accepted incompatible memory range: {ranges}")

for size, limit in (("16GiB", 256), ("1GiB", 0), ("1GiB", 1)):
    try:
        Ramulator2Memory(args.config, size=size, max_outstanding=limit)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Accepted invalid size/limit: {size}, {limit}")

shifted = AddrRange(start=0x10000000, size="3GiB")
memory.set_memory_range([shifted])
assert memory.get_size() == shifted.size()
assert len(memory.get_mem_ports()) == 1
assert memory.get_memory_controllers() == memory.get_mem_interfaces()
assert memory.get_uninterleaved_range() == [shifted]
print("RAMULATOR_CONFIGURATION_OK")
