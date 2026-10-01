import csv
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from wall_follow_benchmark.comparison import load_comparison, load_run, aggregate_runs, metric_statistics

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WORKSPACE/'scripts'))
from build_dashboard import build_dashboard


def create_run(path, error=1., rate=20., times=(0.,1.,2.,3.), events=None, missing=False, architecture='test-cpu'):
    path.mkdir(parents=True)
    metadata=dict(schema_version=2,implementation='cpp',parameters=dict(control_hz=rate,target_distance=.8),duration=4.,completed=True,
                  parameter_events=events or [],
                  compiler='test',source_sha256={'core.hpp':'same-core','controller.cpp':'same-node'})
    (path/'metadata.json').write_text(json.dumps(metadata))
    (path/'experiment.json').write_text(json.dumps(dict(architecture=architecture,gui=False,sensor=dict(lidar_hz=20.))))
    rows=[]
    for t in times:
        rows.append(dict(elapsed=t,sim_time=t,pose_stamp=float('nan') if missing else t,
                         target_distance=.8,wall_elapsed=t,
                         compute_ms=1.+error,scan_age=.01,dt_sim=1.,dt_wall=1.,
                         state='tracking',linear_cmd=.3,x=0.,y=4.-(.8+error)))
    with (path/'samples.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with (path/'poses.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['sim_time','stamp','x','y']);writer.writeheader()
        writer.writerows(dict(sim_time=r['sim_time'],stamp=r['pose_stamp'],x=r['x'],y=r['y']) for r in rows)
    return path


def test_equal_run_weight_not_sample_weight(tmp_path):
    a=create_run(tmp_path/'a',error=1,times=(0.,1.,2.,3.))
    b=create_run(tmp_path/'b',error=3,times=tuple(np.arange(0.,3.01,.25)))
    groups,errors=load_comparison([a,b,a],warmup=0)
    assert errors==[] and len(groups)==1 and groups[0]['count']==2
    st=groups[0]['metric_stats']['rmse_m']
    assert st['mean']==pytest.approx(2.)
    assert st['std']==pytest.approx(2**.5)
    assert st['n']==2
    assert groups[0]['series']['gt_error']==pytest.approx([2.]*4)
    assert groups[0]['series_std']['gt_error']==pytest.approx([2**.5]*4)


def test_grouping_config_provenance_and_runtime_changes(tmp_path):
    runs=[create_run(tmp_path/'a'),create_run(tmp_path/'b',rate=40.),
          create_run(tmp_path/'c',events=[dict(sim_time=2,parameters=dict(control_hz=40.))]),
          create_run(tmp_path/'d',architecture='other-cpu')]
    groups,_=load_comparison(runs,0)
    assert len(groups)==4
    meta=json.loads((runs[0]/'metadata.json').read_text());meta['source_sha256']['core.hpp']='new'
    e=create_run(tmp_path/'e');(e/'metadata.json').write_text(json.dumps(meta))
    assert len(load_comparison([runs[0],e],0)[0])==2


def test_missing_metrics_and_singleton_uncertainty(tmp_path):
    a=create_run(tmp_path/'a',missing=True)
    b=create_run(tmp_path/'b',error=2)
    g=load_comparison([a,b],0)[0][0]
    assert g['count']==2
    assert g['metric_stats']['rmse_m']==pytest.approx(dict(mean=2.,std=None,median=2.,min=2.,max=2.,n=1))
    assert g['series_n']['gt_error']==[1]*4
    assert g['series_std']['gt_error']==[None]*4
    assert metric_statistics([None,float('nan')])['n']==0


def test_no_extrapolation_and_no_common_interval(tmp_path):
    a=create_run(tmp_path/'a',times=(0.,1.,2.))
    b=create_run(tmp_path/'b',times=(1.,2.,3.))
    assert load_comparison([a,b],0)[0][0]['interval']==[1.,2.]
    c=create_run(tmp_path/'c',times=(4.,5.))
    g=load_comparison([a,c],0)[0][0]
    assert g['interval'] is None and g['series']['elapsed']==[]
    assert g['metric_stats']['rmse_m']['n']==2


def test_dashboard_escapes_data_and_preserves_members(tmp_path):
    a=create_run(tmp_path/'a',architecture='</script><script>alert(1)</script>')
    b=create_run(tmp_path/'b',architecture='</script><script>alert(1)</script>')
    output=tmp_path/'dashboard.html'
    data=build_dashboard([a,b],output,0)
    text=output.read_text()
    assert '</script><script>alert(1)</script>' not in text
    assert '\\u003c/script\\u003e' in text
    assert data['runs'][0]['count']==2
    assert len(data['runs'][0]['members'])==2
    assert all(marker not in text for marker in ('__DATA__','__SCRIPT__','__STYLE__'))


def test_failed_attempt_never_pooled_with_success(tmp_path):
    a=create_run(tmp_path/'success')
    b=create_run(tmp_path/'failed')
    (b/'attempt.json').write_text(json.dumps(dict(status='failed',returncode=1)))
    groups,_=load_comparison([a,b],0)
    assert len(groups)==2
    assert {g['completed'] for g in groups}=={True,False}


def test_incomplete_gem5_stats_keeps_failed_robot_data(tmp_path):
    run = create_run(tmp_path/'failed')
    (run/'attempt.json').write_text(json.dumps(dict(status='failed', returncode=1)))
    (run/'gem5').mkdir()
    (run/'gem5/stats.txt').write_text('---------- Begin Simulation Statistics ----------\nsimInsts 120\n')
    groups, errors = load_comparison([run], 0)
    assert len(errors) == 1 and 'gem5 statistics unavailable' in errors[0]
    assert groups[0]['completed'] is False
    assert groups[0]['metric_stats']['rmse_m']['n'] == 1
    assert groups[0]['metric_stats']['gem5_instructions']['n'] == 0


def test_unsupported_schema_is_rejected(tmp_path):
    run=create_run(tmp_path/'unsupported')
    metadata=json.loads((run/'metadata.json').read_text())
    metadata.pop('schema_version')
    (run/'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='Unsupported results schema'):
        load_run(run,0)


def write_timing(path, scale=1.):
    header = ('sim_seconds,gem5_sim_seconds,gem5_wall_seconds,gazebo_wall_seconds,'
              'other_wall_seconds,pacing_wall_seconds,wall_seconds,elapsed_wall_seconds,startup_wall_seconds\n')
    (path/'timing.csv').write_text(header +
        f'1,1,{scale},2,1,1,{scale+4},{scale+4},10\n' +
        f'2,2,{2*scale},4,2,2,{2*scale+8},{3*scale+12},10\n')


def test_timing_equal_run_weight_missing_and_warmup(tmp_path):
    a, b, c = [create_run(tmp_path/n) for n in ('a','b','c')]
    write_timing(a, 1.)
    write_timing(b, 3.)
    groups, errors = load_comparison([a,b,c], warmup=2.)
    assert not errors and len(groups) == 1
    stats = groups[0]['metric_stats']
    assert stats['timing_gem5_phase_realtime_factor']['mean'] == pytest.approx((1+1/3)/2)
    assert stats['timing_gem5_phase_realtime_factor']['n'] == 2
    assert stats['timing_gem5_phase_realtime_factor']['std'] == pytest.approx(np.std([1,1/3],ddof=1))
    assert stats['timing_sim_seconds']['mean'] == 3  # Warmup does not crop timing intervals.
    assert stats['timing_startup_wall_seconds']['mean'] == 10
    assert groups[0]['count'] == 3


def test_timing_summary_fallback_and_bad_timing_keeps_robot_data(tmp_path):
    a, b = [create_run(tmp_path/n) for n in ('a','b')]
    (a/'timing_summary.json').write_text(json.dumps(dict(schema_version=1,
        sim_seconds=2, gem5_sim_seconds=2, gem5_wall_seconds=4, elapsed_wall_seconds=10,
        cosim_realtime_factor=.2)))
    assert load_run(a,0)['metrics']['timing_cosim_wall_seconds_per_sim_second'] == 5
    (b/'timing.csv').write_text('sim_seconds\nnan\n')
    groups, errors = load_comparison([a,b],0)
    assert len(errors) == 1 and 'timing unavailable' in errors[0]
    assert groups[0]['metric_stats']['rmse_m']['n'] == 2
    assert groups[0]['metric_stats']['timing_cosim_realtime_factor']['n'] == 1


def test_timing_config_separates_pacing_settings(tmp_path):
    a, b, c = [create_run(tmp_path/n) for n in ('a','b','c')]
    for path, ratio in ((a,1),(b,10)):
        (path/'attempt.json').write_text(json.dumps(dict(status='completed',
            command=['ros2','launch','wall_follow_bridge','bringup.launch.py',f'ratio:={ratio}'])))
        write_timing(path)
    (c/'attempt.json').write_text(json.dumps(dict(status='completed', pacing='none',
        command=['ros2','launch','wall_follow_bridge','bringup.launch.py'])))
    write_timing(c)
    assert load_run(c,0)['config']['cosimulation.pacing'] == 'none'
    assert len(load_comparison([a,b,c],0)[0]) == 3


def test_timing_outputs_for_mixed_runs(tmp_path):
    from wall_follow_benchmark.comparison import write_summaries
    from wall_follow_benchmark.plot import timing_plot
    a = create_run(tmp_path/'gem5', architecture='gem5')
    b = create_run(tmp_path/'local', architecture='desktop')
    write_timing(a)
    output = tmp_path/'comparison'
    payload = build_dashboard([a,b], output/'dashboard.html',0)
    write_summaries(payload['runs'],output)
    timing_plot(payload['runs'],output)
    assert (output/'timing.png').stat().st_size > 1000
    with (output/'summary.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert 'timing_cosim_realtime_factor_mean' in rows[0]
    assert sorted(int(row['timing_cosim_realtime_factor_n']) for row in rows) == [0,1]
    assert ['timing_cosim_realtime_factor','Overall co-simulation rate (sim s / wall s)'] in payload['timing_metrics']
    assert 'warmup' in payload['timing_scope']
    assert 'id="timing-chart"' in (output/'dashboard.html').read_text()
    import shutil
    import subprocess
    if shutil.which('node'):
        subprocess.run(['node', str(WORKSPACE/'tests/dashboard_smoke.cjs'),
                        str(output/'dashboard.html')], check=True, capture_output=True, text=True)
