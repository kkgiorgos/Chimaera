import math
import pytest
from wall_follow_benchmark.analysis import summarize


def row(t,error):
    return dict(elapsed=t,gt_error=error,wall_elapsed=t,path_m=t*.3)


def test_time_weighting():
    result=summarize([row(0,1),row(1,3),row(4,100)],warmup=0)
    assert result['mae_m']==pytest.approx(2.5)
    assert result['rmse_m']==pytest.approx(7**.5)
    assert result['real_time_factor']==pytest.approx(1)


def test_missing_ground_truth_counts_against_coverage():
    result=summarize([row(0,1),row(1,float('nan')),row(4,0)],warmup=0)
    assert result['gt_coverage']==pytest.approx(.25)
    assert result['rmse_m']==1


def test_empty_window():
    result=summarize([row(0,1),row(1,3)],warmup=5)
    assert result['rmse_m'] is None
    assert result['gt_coverage'] is None
    assert result['path_m'] is None


def test_raw_metrics_use_all_odometry_and_sample_target():
    from wall_follow_benchmark.analysis import derive_metrics
    rows = [dict(sim_time=1., pose_stamp=.9, x=0., y=-3., target_distance=.8),
            dict(sim_time=2., pose_stamp=1., x=1., y=-3., target_distance=1.)]
    poses = [dict(sim_time=.5, x=0., y=-3.), dict(sim_time=.7, x=0., y=-2.),
             dict(sim_time=.9, x=0., y=-3.), dict(sim_time=1.5, x=1., y=-3.)]
    derive_metrics(rows, poses, dict(arena_width=12., arena_height=8.))
    assert rows[0]['gt_error'] == pytest.approx(.2)
    assert rows[0]['path_m'] == 2.
    assert rows[1]['path_m'] == 3.
    assert math.isnan(rows[1]['gt_error'])


def test_warmup_clips_partial_intervals():
    result = summarize([row(0,1), row(1,3), row(4,100)], warmup=.5)
    assert result['mae_m'] == pytest.approx((.5+9)/3.5)
    assert result['path_m'] == pytest.approx(1.05)
    assert result['gt_coverage'] == 1


def test_short_run_retains_task_metrics_by_default():
    result = summarize([row(0,1), row(1,3), row(4.9,100)])
    assert result['rmse_m'] is not None
    assert result['path_m'] == pytest.approx(1.47)
