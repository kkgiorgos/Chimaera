#!/usr/bin/env python3
"""Compare middleware paths only after validating their matched native controls."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiling/chimaera_rewrite'))
from summarize import MATCH_FIELDS, fingerprints, summarize

CPU_FIELDS = ('cpu_type', 'num_cores', 'cpu_clock', 'l1d_size', 'l1i_size', 'l2_size',
              'l1_assoc', 'l2_assoc')


def compare(record):
    if not record.get('comparisons_validated'):
        raise ValueError('Require complete, matched acceptance measurements')
    cases, runs = {}, []
    for attempt in record['attempts']:
        if attempt['result'] != 'complete' or not attempt.get('summary'):
            raise ValueError('Incomplete attempt in acceptance data')
        summary = attempt['summary']
        config = summary['config']
        mode = config['mode']
        label = config.get('middleware_transport', 'bridge') if mode == 'chimaera' else mode
        key = (config['workload'], label)
        if key in cases:
            raise ValueError('Ambiguous repeated case: ' + str(key))
        cases[key] = attempt
        if mode == 'chimaera':
            expected = dict(timing='TimingSimple', kvm='Kvm', o3='O3')[config['cpu_type']]
            cpus = attempt.get('cpu_origin', {}).get('active_cpus', [])
            if len(cpus) != config['num_cores'] or any(expected not in cpu for cpu in cpus):
                raise ValueError('Active CPU provenance differs from the requested model')
        runs.append(dict(path=attempt['case'], variant='middleware', config=config,
            metrics=summary.get('streams') or summary['directions'],
            physical_host=attempt['provenance']['host']))
    summarize(runs)  # Recheck both native controls and binary/DDS fingerprints.
    rows = []
    for workload in sorted({key[0] for key in cases}):
        selected = {name: cases[(workload, name)] for name in
                    ('native_free', 'native_stepped', 'bridge', 'fastdds')}
        before, after = [selected[name]['summary']['config'] for name in ('bridge', 'fastdds')]
        fields = (*MATCH_FIELDS, *CPU_FIELDS, 'image', 'guest_bundle')
        if any(before.get(key) != after.get(key) for key in fields) or (
                fingerprints(before) != fingerprints(after)) or (
                selected['bridge']['provenance']['gem5_sha256'] !=
                selected['fastdds']['provenance']['gem5_sha256']):
            raise ValueError('Bridge/Fast DDS comparison settings differ')
        metrics = {}
        for name, attempt in selected.items():
            summary = attempt['summary']
            metrics[name] = dict(summary.get('streams') or summary['directions'])
            if 'stereo_pairs' in summary:
                metrics[name]['stereo_pair'] = summary['stereo_pairs']
        valid = all(m['published'] > 0 and m['published'] == m['received'] and
                    not m.get('missing_at_drain', 0) and not m.get('duplicates', 0) and
                    not m.get('reordered', 0) for name in ('bridge', 'fastdds')
                    for m in metrics[name].values())
        for stream in metrics['bridge']:
            values = {name: metric[stream] for name, metric in metrics.items()}
            legacy, middleware = values['bridge'], values['fastdds']
            equal_count = legacy['published'] == middleware['published']
            old, new = legacy['latency']['mean_ms'], middleware['latency']['mean_ms']
            reduction = (100 * (old - new) / old
                         if valid and equal_count and old is not None and old > 0 else None)
            rows.append(dict(workload=workload, stream=stream, delivery_valid=valid,
                equal_source_count=equal_count, mean_reduction_percent=reduction, metrics=values))
    return dict(physical_host=record['physical_host'], rows=rows,
        definition='Publication-to-callback delay in co-simulation milliseconds; age starts at '
                   'scheduled release. Native timestamps use 1 ms buckets.',
        interpretation='Percent reductions require complete, unique, ordered delivery and equal '
                       'source counts. Negative reductions indicate increased mean delay.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempts', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.attempts.read_bytes()
    result = compare(json.loads(source))
    result['source'] = dict(path=str(args.attempts), sha256=hashlib.sha256(source).hexdigest())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'middleware-comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    rows = []
    for row in result['rows']:
        flat = {key: value for key, value in row.items() if key != 'metrics'}
        for name, metric in row['metrics'].items():
            flat.update({name + '_' + key: metric[key] for key in ('published', 'received')})
            for stat in ('mean_ms', 'p95_ms', 'p99_ms', 'max_ms'):
                flat[name + '_' + stat] = metric['latency'].get(stat)
            flat[name + '_age_mean_ms'] = metric.get('age', {}).get('mean_ms')
        rows.append(flat)
    with (args.output / 'middleware-comparison.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    print(f'{len(rows)} matched middleware stream/pair comparisons')


if __name__ == '__main__':
    main()
