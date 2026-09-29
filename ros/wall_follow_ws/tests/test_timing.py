from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'benchmarking'))
from wall_follow_benchmark.timing import summarize


def test_weighted_rates(tmp_path):
    path = tmp_path/'timing.csv'
    path.write_text('sim_seconds,gem5_sim_seconds,gem5_wall_seconds,gazebo_wall_seconds,other_wall_seconds,pacing_wall_seconds,wall_seconds,elapsed_wall_seconds,startup_wall_seconds\n'
                    '1,1.1,2,1,.5,.5,4,4,10\n2,1.9,3,1,.5,.5,5,9,10\n')
    result = summarize(path)
    assert result['cosim_realtime_factor'] == pytest.approx(3/9)
    assert result['unpaced_realtime_factor'] == pytest.approx(3/8)
    assert result['gem5_phase_realtime_factor'] == pytest.approx(3/5)
    assert result['gazebo_phase_realtime_factor'] == 1.5
    assert result['startup_wall_seconds'] == 10
    assert result['cosim_wall_seconds_per_sim_second'] == 3
    assert result['gem5_wall_seconds_per_sim_second'] == pytest.approx(5/3)


def test_empty_timing_rejected(tmp_path):
    path = tmp_path/'timing.csv'
    path.write_text('sim_seconds\n')
    with pytest.raises(ValueError, match='No completed'):
        summarize(path)
