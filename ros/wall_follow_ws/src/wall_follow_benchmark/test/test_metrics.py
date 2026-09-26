import pytest
from wall_follow_benchmark.plot import summarize


def row(t,error):
    return dict(elapsed=t,gt_error=error,wall_elapsed=t,compute_ms=1.,scan_age=.02,
                cpu_seconds=t*.1,state='tracking',linear_cmd=.3,path_m=t*.3,rss_kib=10240)


def test_time_weighting():
    result=summarize([row(0,1),row(1,3),row(4,100)],warmup=0)
    assert result['mae_m']==pytest.approx(2.5)
    assert result['rmse_m']==pytest.approx(7**.5)
    assert result['real_time_factor']==pytest.approx(1)
    assert result['controller_cpu_core_fraction']==pytest.approx(.1)


def test_missing_ground_truth_counts_against_coverage():
    result=summarize([row(0,1),row(1,float('nan')),row(4,0)],warmup=0)
    assert result['gt_coverage']==pytest.approx(.25)
    assert result['rmse_m']==1


def test_empty_window():
    result=summarize([row(0,1),row(1,3)],warmup=5)
    assert result['rmse_m'] is None
    assert result['gt_coverage']==0
