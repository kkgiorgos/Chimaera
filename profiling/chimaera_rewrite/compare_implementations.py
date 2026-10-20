#!/usr/bin/env python3
"""Compare delivered throughput and latency without mixing benchmark cohorts."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from summarize import MATCH_FIELDS, fingerprints, summarize


LABELS = {
    'baseline': 'Original',
    'candidate-v1': 'Streamed rewrite',
    'candidate-v2': 'Buffer rewrite',
    'm5': 'M5 rewrite',
    'dma': 'Integrated DMA',
}


def throughput(metric, duration, body_bytes):
    """Count a source cohort including its drain; this is not maximum capacity."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Source duration must be finite and positive')
    published, received = metric['published'], metric['received']
    if published <= 0 or not 0 <= received <= published:
        raise ValueError('Invalid source/delivery counts')
    if metric.get('duplicates', 0):
        raise ValueError('Throughput requires unique delivery counts')
    rate = received / duration
    return dict(delivery_fraction=received / published,
                offered_per_source_second=published / duration,
                delivered_per_source_second=rate,
                payload_body_bytes=body_bytes,
                payload_MB_per_source_second=(rate * body_bytes / 1e6
                                              if body_bytes is not None else None))


def acceptance_runs(record):
    if not record.get('comparisons_validated'):
        raise ValueError('Require matched acceptance, not diagnostic history')
    runs = []
    for attempt in record['attempts']:
        if attempt['result'] != 'complete' or not attempt.get('summary'):
            raise ValueError('Acceptance contains an incomplete attempt')
        summary = attempt['summary']
        config = summary['config']
        if config['mode'] == 'chimaera':
            cpus = attempt.get('cpu_origin', {}).get('active_cpus', [])
            if config['cpu_type'] != 'timing' or len(cpus) != config['num_cores'] or any(
                    'TimingSimple' not in cpu for cpu in cpus):
                raise ValueError('Require active detailed CPU provenance')
        runs.append(dict(path=attempt['case'], variant=attempt['case'].split('/')[0],
                         config=config,
                         metrics=summary.get('streams') or summary['directions'],
                         pair=summary.get('stereo_pairs'),
                         physical_host=attempt['provenance']['host']))
    summarize(runs)  # Recheck both native controls and their binary/profile identities.
    # The native check is within each backend. Also require equal traffic, CPU
    # geometry, endpoint binaries and DDS bytes across the M5/DMA comparison.
    fields = [f for f in MATCH_FIELDS if f != 'transport_backend'] + [
        'cpu_type', 'num_cores', 'cpu_clock', 'l1d_size', 'l1i_size', 'l2_size',
        'l1_assoc', 'l2_assoc',
    ]
    simulated = [r for r in runs if r['config']['mode'] == 'chimaera']
    for run in simulated:
        c = run['config']
        peers = [r for r in simulated
                 if r['config']['workload'] == c['workload']
                 and r['config']['delivery_policy'] == c['delivery_policy']
                 and r['config']['transport_backend'] != c['transport_backend']]
        if len(peers) != 1 or any(peers[0]['config'].get(f) != c.get(f) for f in fields) or (
                fingerprints(peers[0]['config']) != fingerprints(c)):
            raise ValueError('M5/DMA acceptance settings differ')
    return runs


def rows_for(runs, cohort):
    rows = []
    for run in runs:
        c = run['config']
        impl = c['transport_backend'] if cohort == 'acceptance' else run['variant']
        metrics = dict(run['metrics'])
        if run.get('pair'):
            metrics['stereo_pair'] = run['pair']
        for stream, m in metrics.items():
            if stream == 'stereo_pair':
                body_bytes = sum(metrics[k]['body_bytes'] for k in
                                 ('left_image', 'right_image'))
            elif c['workload'] == 'synthetic':
                body_bytes = c['payload_bytes']
            else:
                body_bytes = m.get('body_bytes')
            row = dict(cohort=cohort, implementation=impl, label=LABELS[impl],
                       case=run['path'], mode=c['mode'], workload=c['workload'],
                       policy=c.get('delivery_policy', 'latest'), stream=stream,
                       source_seconds=c['duration'], drain_seconds=c['drain'],
                       published=m['published'], received=m['received'],
                       missing_at_drain=m['published'] - m['received'],
                       duplicates=m.get('duplicates'), reordered=m.get('reordered'),
                       wall_seconds=c['wall_seconds'])
            row.update(throughput(m, c['duration'], body_bytes))
            for prefix, key in (('latency', 'latency'), ('age', 'age')):
                for stat in ('mean', 'p99', 'max'):
                    row[f'{prefix}_{stat}_ms'] = m.get(key, {}).get(f'{stat}_ms')
            rows.append(row)
    return rows


def export(pilots, acceptance, output):
    historical = json.loads(pilots.read_text())
    historical = [r for r in historical if r['config'].get('cpu') != 'kvm'
                  and r['config'].get('cpu_type') != 'kvm']
    summarize(historical)
    accepted = acceptance_runs(json.loads(acceptance.read_text()))
    rows = rows_for(historical, 'historical') + rows_for(accepted, 'acceptance')
    data = dict(
        physical_host='ubuntu-george',
        throughput_definition='Unique receipts from source cohort, including drain, divided '
                              'by source-window seconds. Fixed offered load; no capacity sweep.',
        payload_definition='Application body only; decimal MB. Stereo-pair rows include '
                           'both pixel buffers. Pair and per-eye rows overlap; do not sum them.',
        latency_definition='Actual source publication to callback in co-simulation milliseconds. '
                           'Age starts at scheduled release. Native clocks have 1 ms buckets.',
        cohorts={
            'historical': '0.2 s source / 0.05 s warmup / 0.15 s drain. Legacy DDS and endpoints; '
                          '6 frames per eye; exploratory. No exported pair-age metric.',
            'acceptance': '2 s source / 0.25 s warmup / 0.5 s control or 0.75 s stereo drain. '
                          'Matched endpoints and 16 MiB DDS profile; 60 stereo pairs. One repeat.',
        },
        sources=[dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                 for p in (pilots, acceptance)],
        cases=dict(historical=len(historical), acceptance=len(accepted)), rows=rows,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(data, indent=2) + '\n')
    with (output / 'comparison.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    template = Path(__file__).with_name('implementation_comparison.html').read_text()
    (output / 'report.html').write_text(template.replace(
        'const data=DATA;', 'const data=' + json.dumps(data).replace('<', '\\u003c') + ';'))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilots', type=Path, default=Path(
        'docs/measurements/chimaera-rewrite-2026-10-20/runs.json'))
    parser.add_argument('--acceptance', type=Path, default=Path(
        'docs/measurements/chimaera-dma-integration-2026-10-20/acceptance/attempts.json'))
    parser.add_argument('--output', type=Path, default=Path(
        'docs/measurements/chimaera-implementation-comparison-2026-10-20'))
    args = parser.parse_args()
    data = export(args.pilots, args.acceptance, args.output)
    print(f"{data['cases']}; {len(data['rows'])} throughput/latency rows")


if __name__ == '__main__':
    main()
