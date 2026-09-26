"""Load results and aggregate independent repetitions of identical experiments."""
import csv
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from .analysis import summarize, run_label

SERIES = ('elapsed', 'gt_error', 'x', 'y', 'compute_ms', 'scan_age', 'dt_wall', 'path_m')


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (float, np.floating)) and not math.isfinite(value):
        return None
    return value


def flatten(value, prefix=''):
    result = {}
    for key,item in value.items():
        name = f'{prefix}.{key}' if prefix else key
        if isinstance(item, dict):
            result.update(flatten(item, name))
        else:
            result[name] = item
    return result


def load_run(path, warmup):
    path = Path(path)
    metadata = json.loads((path/'metadata.json').read_text())
    experiment_file = path/'experiment.json'
    experiment = json.loads(experiment_file.read_text()) if experiment_file.exists() else {}
    with (path/'samples.csv').open(newline='') as f:
        rows = list(csv.DictReader(f))
    if len(rows)<2:
        raise ValueError('requires at least two samples')
    times = [float(row['elapsed']) for row in rows]
    if any(not math.isfinite(t) for t in times) or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('elapsed times must be finite and strictly increasing')
    arena = metadata.get('arena', experiment.get('arena', dict(arena_width=12., arena_height=8.)))
    config = {**experiment, 'controller':metadata.get('parameters', {}), 'arena':arena,
              'duration':metadata.get('duration')}
    if config.get('gui') in ('true', 'false'):
        config['gui'] = config['gui']=='true'
    # Presentation-only source changes do not split otherwise identical experiments.
    provenance = {k:metadata[k] for k in ('platform','machine','processor','python','ros_distro') if k in metadata}
    provenance['source_sha256'] = {k:v for k,v in metadata.get('source_sha256', {}).items()
                                  if k in ('core.py','node.py','world.py')}
    events = metadata.get('parameter_events', [])
    attempt_file = path/'attempt.json'
    attempt = json.loads(attempt_file.read_text()) if attempt_file.exists() else None
    completed = metadata.get('completed') is True and (attempt is None or attempt.get('status') == 'completed')
    if metadata.get('final_parameters', config['controller']) != config['controller']:
        config['final_controller'] = metadata['final_parameters']
    return dict(name=run_label(path), path=str(path.resolve()), completed=completed,
                config=flatten(config), provenance=flatten(provenance), events=events, arena=arena,
                final_parameters=metadata.get('final_parameters', {}), metrics=summarize(rows,warmup),
                sample_count=len(rows), series={key:np.array([float(r.get(key,'nan')) for r in rows]) for key in SERIES})


def identity(run):
    return json.dumps(clean(dict(config=run['config'], provenance=run['provenance'],
                                events=run['events'], completed=run['completed'])),sort_keys=True,allow_nan=False)


def metric_statistics(values):
    valid = np.asarray([v for v in values if v is not None and math.isfinite(v)],dtype=float)
    if not len(valid):
        return dict(mean=None,std=None,median=None,min=None,max=None,n=0)
    return dict(mean=float(valid.mean()), std=float(valid.std(ddof=1)) if len(valid)>1 else None,
                median=float(np.median(valid)),min=float(valid.min()),max=float(valid.max()),n=len(valid))


def aggregate_series(runs, max_points):
    start = max(r['series']['elapsed'][0] for r in runs)
    end = min(r['series']['elapsed'][-1] for r in runs)
    if end <= start:
        return dict(elapsed=[]), {}, {}, None
    step = max(float(np.median(np.diff(r['series']['elapsed']))) for r in runs)
    count = min(max_points, max(2,int(math.ceil((end-start)/step))+1))
    grid = np.linspace(start,end,count)
    means, stds, counts = {'elapsed':grid}, {}, {}
    for key in SERIES[1:]:
        # Interpolate only within each run's recorded interval. NaN gaps stay missing.
        values = np.array([np.interp(grid,r['series']['elapsed'],r['series'][key]) for r in runs])
        finite = np.isfinite(values)
        n = finite.sum(axis=0)
        mean = np.divide(np.where(finite,values,0).sum(axis=0),n,
                         out=np.full(count,np.nan),where=n>0)
        deviations = np.where(finite,values-mean,0)
        variance = np.divide((deviations**2).sum(axis=0),n-1,
                             out=np.full(count,np.nan),where=n>1)
        means[key],stds[key],counts[key] = mean,np.sqrt(variance),n
    return clean(means),clean(stds),clean(counts),[float(start),float(end)]


def aggregate_runs(runs, max_points=1500):
    buckets = {}
    for run in runs:
        buckets.setdefault(identity(run),[]).append(run)
    groups = []
    for signature,members in sorted(buckets.items()):
        first = members[0]
        group_id = hashlib.sha256(signature.encode()).hexdigest()[:12]
        config = first['config']
        width,height = first['arena']['arena_width'],first['arena']['arena_height']
        name = f"{config.get('architecture','unknown')} · {config.get('controller.control_hz','?')} Hz · {width:g}×{height:g} m · {group_id[:6]}"
        stats = {key:metric_statistics([r['metrics'].get(key) for r in members]) for key in first['metrics']}
        means,stds,counts,interval = aggregate_series(members,max_points)
        groups.append(clean(dict(id=group_id,name=name,count=len(members),path='\n'.join(r['path'] for r in members),
            completed=first['completed'],config=config,provenance=first['provenance'],events=first['events'],
            final_parameters=first['final_parameters'],arena=first['arena'],
            sample_count=sum(r['sample_count'] for r in members),
            metrics={k:v['mean'] for k,v in stats.items()},metric_stats=stats,
            series=means,series_std=stds,series_n=counts,interval=interval,
            members=[{k:r[k] for k in ('name','path','metrics','sample_count','completed')} for r in members])))
    return groups


def load_comparison(paths, warmup=5., max_points=1500):
    if not math.isfinite(warmup) or warmup<0 or max_points<2:
        raise ValueError('warmup must be finite and nonnegative; max_points must be at least 2')
    runs, errors = [], []
    for path in sorted({Path(p).resolve() for p in paths}):
        try:
            runs.append(load_run(path,warmup))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            errors.append(f'{path}: {exc}')
    if not runs:
        raise ValueError('No readable runs. '+'; '.join(errors))
    return aggregate_runs(runs,max_points), errors


def write_summaries(groups, output):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    records = []
    for g in groups:
        row = dict(group_id=g['id'],label=g['name'],repetitions=g['count'],completed=g['completed'],
                   configuration=json.dumps(g['config'],sort_keys=True), provenance=json.dumps(g['provenance'],sort_keys=True),
                   parameter_events=json.dumps(g['events']),
                   members=json.dumps([r['path'] for r in g['members']]))
        for metric,stats in g['metric_stats'].items():
            row.update({f'{metric}_{stat}':value for stat,value in stats.items()})
        records.append(row)
    (output/'summary.json').write_text(json.dumps(records,indent=2,allow_nan=False))
    with (output/'summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    (output/'per_run_summary.json').write_text(json.dumps(
        [dict(group_id=g['id'],**member) for g in groups for member in g['members']],indent=2,allow_nan=False))
    return records


def configuration_rows(groups):
    records = [{**g['config'], **{'provenance.'+k:v for k,v in g['provenance'].items()},
                'runtime.parameter_events':g['events'], 'runtime.completed':g['completed']} for g in groups]
    rows = []
    for key in sorted({key for record in records for key in record}):
        values = [record.get(key) for record in records]
        differs = len({json.dumps(v,sort_keys=True) for v in values})>1
        rows.append(dict(setting=key,differs=differs,**{g['id']:v for g,v in zip(groups,values)}))
    return rows
