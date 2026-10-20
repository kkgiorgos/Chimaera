#!/usr/bin/env python3
"""Export analysis JSON and reviewable CSV evidence from raw profiling cases."""
import argparse
import csv
import json
from pathlib import Path

from analyze import analyze


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def measurement_rows(name, summary, variant='existing'):
    config = summary['config']
    for label, value in (summary['streams'] or summary['directions']).items():
        yield dict(case=name, variant=variant, mode=config['mode'],
                   workload=config.get('workload', 'synthetic'), cpu=config.get('cpu'),
                   cores=config.get('num_cores'), interval_us=config['interval_us'],
                   poll_us=config['poll_us'], payload_bytes=config['payload_bytes'],
                   source_rate_hz=value.get('rate_hz', config['rate_hz']),
                   duration=config['duration'], warmup=config['warmup'], drain=config['drain'],
                   repeat=config.get('repeat'), stream=label,
                   direction=value.get('direction', label), published=value['published'],
                   received=value['received'], delivery_fraction=value['delivery_fraction'],
                   actual_source_hz=value['actual_source_hz'],
                   release_lag_mean_ms=value['release_lag']['mean_ms'],
                   case_wall_seconds=config.get('wall_seconds'), **value['latency'])


def export(roots, output, existing=None):
    output.mkdir(parents=True, exist_ok=True)
    results = [analyze(path.parent) for root in roots for path in sorted(root.glob('*/run.json'))]
    (output/'analysis.json').write_text(json.dumps(results, indent=2, allow_nan=False)+'\n')
    metrics, stages, phases, pipelines, quality = [], [], [], [], []
    for result in results:
        name = Path(result['directory']).name
        provenance = result.get('profile_provenance') or {}
        variant = provenance.get('case', {}).get('variant', 'unknown')
        quality.append(dict(case=name, status=result['config']['status'], variant=variant,
                            trace_host_rows=result.get('trace_rows', {}).get('host', 0),
                            trace_guest_rows=result.get('trace_rows', {}).get('guest', 0),
                            error=result['config'].get('error')))
        if 'summary' not in result:
            continue
        metrics.extend(measurement_rows(name, result['summary'], variant))
        for path, decomposition in result['decomposition'].items():
            for stage, value in decomposition['stages'].items():
                stages.append(dict(case=name, path=path, stage=stage,
                                   complete_paths=decomposition['complete_paths'],
                                   incomplete_paths=decomposition['incomplete_paths'],
                                   nonmonotonic_paths=decomposition['nonmonotonic_paths'],
                                   maximum_backwards_ms=decomposition['maximum_backwards_ms'],
                                   cohort_mean_ms=decomposition['latency']['mean_ms'], **value))
        wall = result['wall_phases']
        for phase, value in wall['phases'].items():
            phases.append(dict(case=name, phase=phase, steps=wall['steps'],
                               whole_step_mean_ms=wall['whole_step']['mean_ms'], **value))
        for path, pipeline in result['pipelines'].items():
            for stage, count in pipeline['stages'].items():
                pipelines.append(dict(case=name, path=path, stage=stage, observed=count,
                                      published=pipeline['published'],
                                      application_receipts=pipeline['application_receipts']))
    for filename, rows in [('run-summary.csv',metrics), ('stage-breakdown.csv',stages),
                           ('wall-phases.csv',phases), ('pipeline-counts.csv',pipelines),
                           ('run-quality.csv',quality)]:
        write_csv(output/filename, rows)
    if existing:
        rows = [row for path in sorted(existing.glob('*/*/summary.json'))
                for row in measurement_rows(str(path.parent.relative_to(existing)),
                                            json.loads(path.read_text()))]
        write_csv(output/'existing-summary.csv', rows)
    return results


def plot(results, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    by_name = {Path(result['directory']).name: result for result in results}
    colors = ['#315889','#408b91','#c78e35','#8960a5','#a75762','#638b45']
    fig, axes = plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    chosen = ['small_s50000_p1000_trace_r1', 'small_s5000_p1000_trace_r1',
              'small_s1000_p100_trace_r1']
    for direction, ax in zip(['host_to_guest','guest_to_host'],axes):
        for index,name in enumerate(chosen):
            if name not in by_name:
                continue
            result = by_name[name]
            d = result.get('decomposition',{}).get(direction+'/synthetic')
            if not d or not d['complete_paths']:
                continue
            left=0
            for color,(label,value) in zip(colors,d['stages'].items()):
                ax.barh(index,value['mean_ms'],left=left,color=color,
                        label=label if index==0 else None)
                left += value['mean_ms']
            ax.text(left+.2,index,f'{left:.2f} ms',va='center',fontsize=9)
        ax.set_yticks(range(3),['50 ms / 1 ms','5 ms / 1 ms','1 ms / 0.1 ms'])
        ax.invert_yaxis()
        ax.set_title(direction.replace('_',' ').capitalize())
        ax.set_xlabel('Mean latency (simulation ms); traced complete paths')
        ax.spines[['top','right']].set_visible(False)
        ax.grid(axis='x',alpha=.15)
        ax.set_axisbelow(True)
        ax.margins(x=.15)
        ax.legend(loc='upper left',bbox_to_anchor=(0,-.22),fontsize=8,frameon=False)
    common_limit = max(ax.get_xlim()[1] for ax in axes)
    for ax in axes:
        ax.set_xlim(0, common_limit)
    fig.suptitle('64-byte bodies · 97 Hz each direction · two KVM cores\nSynchronization / requested polling',fontsize=12)
    fig.savefig(output/'message-stages.svg',bbox_inches='tight')
    fig.savefig(output/'message-stages.png',dpi=180,bbox_inches='tight')
    plt.close(fig)

    result = by_name.get('small_s1000_p100_trace_r1')
    if result and result['wall_phases']['steps']:
        phases=result['wall_phases'];labels=['Guest execution','Two settling windows','Gazebo completion wait','Requests / bridge / remainder']
        values=[phases['phases']['gem5_phase']['mean_ms'],
                sum(phases['phases'][name]['mean_ms'] for name in ['settle_before_physics','settle_after_physics']),
                phases['phases']['gazebo_wait']['mean_ms']]
        values.append(phases['whole_step']['mean_ms']-sum(values))
        fig,ax=plt.subplots(figsize=(10,2.8),layout='constrained')
        left=0
        for label,value,color in zip(labels,values,colors):
            ax.barh(0,value,left=left,label=f'{label}: {value:.2f} ms',color=color)
            left+=value
        ax.set_yticks([])
        ax.set_xlabel('Host wall milliseconds per 1 ms simulated step')
        ax.set_title(f'{left:.2f} ms wall / step, {phases["steps"]:,} measured steps')
        ax.spines[['top','right','left']].set_visible(False)
        ax.legend(loc='upper left',bbox_to_anchor=(0,-.35),ncols=2,frameon=False,fontsize=9)
        fig.savefig(output/'wall-phases.svg',bbox_inches='tight')
        fig.savefig(output/'wall-phases.png',dpi=180,bbox_inches='tight')
        plt.close(fig)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--existing',type=Path)
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args()
    results=export(args.roots,args.output,args.existing)
    if args.plot:
        plot(results,args.output)
