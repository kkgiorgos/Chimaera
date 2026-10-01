from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmarking'))
from wall_follow_benchmark.gem5_stats import summarize


def test_roi_stats_last_complete_dump(tmp_path):
    stats = tmp_path/'stats.txt'
    stats.write_text('''---------- Begin Simulation Statistics ----------
simInsts 9999
---------- End Simulation Statistics ----------
---------- Begin Simulation Statistics ----------
simSeconds 2
simInsts 120
board.processor.start0.core.numCycles 9999
board.processor.switch0.core.numCycles 100
board.processor.switch1.core.numCycles 200
board.cache_hierarchy.ruby_system.l1_controllers0.L1Dcache.m_demand_misses 4
board.cache_hierarchy.ruby_system.l1_controllers1.L1Dcache.m_demand_misses 6
board.cache_hierarchy.ruby_system.l2_controllers0.L2cache.m_demand_misses 3
---------- End Simulation Statistics ----------
---------- Begin Simulation Statistics ----------
simInsts 123456
''')
    result = summarize(stats)
    assert result['instructions'] == 120
    assert result['cycles'] == 300
    assert result['ipc'] == .4
    assert result['l1d_misses'] == 10
    assert result['l2_misses'] == 3
    assert result['l1i_misses'] is None
    assert result['l1d_mpki'] == pytest.approx(1000 * 10 / 120)
    assert result['l2_mpki'] == 25
    assert result['l1i_mpki'] is None


def test_no_stats_dump(tmp_path):
    stats = tmp_path/'stats.txt'
    stats.write_text('')
    with pytest.raises(ValueError):
        summarize(stats)


@pytest.mark.parametrize('controllers, expected', [
    (['l2_controllers'], 3),
    (['l2_controllers0'], 3),
    (['l2_controllers0', 'l2_controllers1'], 6),
])
def test_l2_single_controller_and_multiple_banks(tmp_path, controllers, expected):
    counters = '\n'.join(
        f'board.cache_hierarchy.ruby_system.{name}.L2cache.m_demand_misses 3'
        for name in controllers)
    stats = tmp_path/'stats.txt'
    stats.write_text('---------- Begin Simulation Statistics ----------\n'
                     + counters + '\n---------- End Simulation Statistics ----------\n')
    assert summarize(stats)['l2_misses'] == expected
