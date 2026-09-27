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
                         compute_ms=1.+error,scan_age=.01,dt_sim=1.,dt_wall=1.,cpu_seconds=.1*t,
                         state='tracking',linear_cmd=.3,rss_kib=10240,x=0.,y=4.-(.8+error)))
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


def test_unsupported_schema_is_rejected(tmp_path):
    run=create_run(tmp_path/'unsupported')
    metadata=json.loads((run/'metadata.json').read_text())
    metadata.pop('schema_version')
    (run/'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='Unsupported results schema'):
        load_run(run,0)
