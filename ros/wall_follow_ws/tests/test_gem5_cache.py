"""Check set mapping from the actual MESI L1 constructor without running gem5."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest


class NumericParameter:
    """Model gem5's integer conversion and identity-based equality."""

    def __init__(self, value):
        self.value = value

    def __int__(self):
        return self.value

    def __float__(self):
        return float(self.value)


def l1_cache_class():
    root = Path(__file__).resolve().parents[3]
    path = root/'gem5/src/python/gem5/components/cachehierarchies/ruby/caches/mesi_two_level/l1_cache.py'
    tree = ast.parse(path.read_text())
    constructor = ast.Module(body=[ast.ImportFrom(module='__future__',
                                names=[ast.alias(name='annotations')], level=0)] +
                                [node for node in tree.body if isinstance(node, ast.ClassDef)],
                             type_ignores=[])

    class Cache:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    namespace = dict(math=math, MESI_Two_Level_L1Cache_Controller=type('Controller', (), {}),
                     RubyCache=Cache, RubyPrefetcher=Cache, MessageBuffer=SimpleNamespace)
    exec(compile(ast.fix_missing_locations(constructor), str(path), 'exec'), namespace)
    return namespace['L1Cache']


@pytest.mark.parametrize('line_size', [32, 64, 128])
@pytest.mark.parametrize('parameter_type', [int, NumericParameter])
def test_l1_set_mapping_uses_all_configured_sets(line_size, parameter_type):
    cache = l1_cache_class()(
        l1i_size=16384, l1i_assoc=8, l1d_size=16384, l1d_assoc=8,
        network=SimpleNamespace(in_port=object(), out_port=object()),
        core=SimpleNamespace(requires_send_evicts=lambda: False),
        num_l2Caches=1, cache_line_size=parameter_type(line_size), target_isa=None, clk_domain=None)
    for memory in [cache.L1Icache, cache.L1Dcache]:
        sets = memory.size // (memory.assoc * line_size)
        index = memory.start_index_bit
        assert index == int(math.log2(line_size))
        mapped = {(address >> index) & (sets - 1)
                  for address in range(0, memory.size, line_size)}
        assert mapped == set(range(sets))


@pytest.mark.parametrize('line_size', [0, -64, 96])
def test_l1_rejects_invalid_line_size(line_size):
    with pytest.raises(ValueError, match='power of two'):
        l1_cache_class()(
            l1i_size=16384, l1i_assoc=8, l1d_size=16384, l1d_assoc=8,
            network=SimpleNamespace(in_port=object(), out_port=object()),
            core=None, num_l2Caches=1, cache_line_size=line_size, target_isa=None, clk_domain=None)
