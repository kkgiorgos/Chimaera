"""Verify embedded MESI cache parameters using gem5, without starting simulation.

Run: gem5/build/X86/gem5.opt --outdir=/tmp/chimaera-components-check THIS_FILE
No image, m5.instantiate(), or m5.simulate() is used.
"""
import json

from m5.objects import SimpleNetwork, SrcClockDomain
from m5.params import Unsigned
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.caches.mesi_two_level.l1_cache import L1Cache
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_core import SimpleCore
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA


def verify_mapping(cache, line_size, expected_bit):
    for memory in [cache.L1Icache, cache.L1Dcache]:
        index = int(memory.start_index_bit)
        assert index == expected_bit, (line_size, index)
        sets = int(memory.size) // (int(memory.assoc) * line_size)
        mapped = {(address >> index) & (sets - 1)
                  for address in range(0, int(memory.size), line_size)}
        assert mapped == set(range(sets))


verified = []
for parameter_type in [int, Unsigned]:
    for line_size, expected_bit in [(32, 5), (64, 6), (128, 7)]:
        cache = L1Cache(
            l1i_size="16KiB", l1i_assoc=8, l1d_size="16KiB", l1d_assoc=8,
            network=SimpleNetwork(),
            core=SimpleCore(cpu_type=CPUTypes.TIMING, core_id=0, isa=ISA.X86),
            num_l2Caches=1, cache_line_size=parameter_type(line_size), target_isa=ISA.X86,
            clk_domain=SrcClockDomain(clock="3GHz"))
        verify_mapping(cache, line_size, expected_bit)
        verified.append(dict(parameter_type=parameter_type.__name__,
                             line_size=line_size, start_index_bit=expected_bit))

# Exercise the benchmark's board-to-hierarchy wiring, including its actual
# Unsigned cache-line parameter. Configure full-system devices without loading
# a kernel/image, instantiating C++ simulation objects, or advancing time.
hierarchy = MESITwoLevelCacheHierarchy(
    l1d_size="16KiB", l1d_assoc=8, l1i_size="16KiB", l1i_assoc=8,
    l2_size="256KiB", l2_assoc=16, num_l2_banks=1)
processor = SimpleSwitchableProcessor(
    starting_core_type=CPUTypes.KVM, switch_core_type=CPUTypes.TIMING,
    num_cores=2, isa=ISA.X86)
board = X86Board(clk_freq="3GHz", processor=processor,
                 memory=SingleChannelDDR3_1600(size="3GiB"), cache_hierarchy=hierarchy)
assert isinstance(board.get_cache_line_size(), Unsigned)
board._set_fullsystem(True)
board._connect_things()
assert len(hierarchy._l1_controllers) == 2
for cache in hierarchy._l1_controllers:
    verify_mapping(cache, 64, 6)
assert int(hierarchy._l2_controllers[0].L2cache.start_index_bit) == 6
print(json.dumps(dict(verified=verified, board_cores=2, board_wiring_verified=True,
                      simulation_started=False)))
