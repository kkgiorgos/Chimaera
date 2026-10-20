#!/usr/bin/env python3
"""Export rewrite measurements, requiring matched native controls on the same host."""
import argparse
import csv
import json
from pathlib import Path


# CPU settings deliberately differ between native and simulated runs. Everything
# affecting traffic generation, clock granularity and coupling must match.
MATCH_FIELDS = (
    'workload', 'payload_bytes', 'rate_hz', 'duration', 'warmup', 'drain',
    'physics_step_ns', 'interval_us', 'poll_us', 'reliability', 'depth', 'ball_catching',
    'delivery_policy', 'verify_payload',
    'transport_backend', 'dds_shm_bytes', 'dds_tuning', 'dds_max_message_bytes',
    'rmw',
)


def identity(config):
    return json.dumps({key: config.get(key) for key in MATCH_FIELDS}, sort_keys=True)


def fingerprints(config):
    return {Path(path).name: digest for path, digest in config.get('artifact_sha256', {}).items()
            if Path(path).name in ('latency_endpoint', 'latency_driver',
                                   'libchimaera_latency_clock.so', 'dds.xml')}


def collect(root):
    runs = []
    for path in sorted(root.glob('*/*/summary.json')):
        data = json.loads(path.read_text())
        config = data['config']
        provenance = path.with_name('profile-provenance.json')
        provenance = json.loads(provenance.read_text()) if provenance.exists() else {}
        runs.append(dict(path=str(path.relative_to(root)), variant=path.parents[1].name,
                         config=config, metrics=data.get('streams') or data['directions'],
                         physical_host=provenance.get('host', config.get('hostname')),
                         provenance=provenance))
    return runs


def summarize(runs):
    rows = []
    for run in runs:
        config = run['config']
        controls = {}
        if config['mode'] == 'chimaera':
            for mode in ('native_free', 'native_stepped'):
                matches = [candidate for candidate in runs
                           if candidate['variant'] == run['variant']
                           and candidate['config']['mode'] == mode
                           and candidate['physical_host'] == run['physical_host']
                           and fingerprints(candidate['config']) == fingerprints(config)
                           and identity(candidate['config']) == identity(config)]
                if not matches:
                    raise ValueError(f"{run['path']}: missing matched {mode} control")
                # Prefer the same repeat; retain all run records in JSON.
                controls[mode] = next((candidate for candidate in matches
                                      if candidate['config'].get('repeat') == config.get('repeat')),
                                     matches[0])
            origin = (Path(run['absolute_path']).parent / 'gem5/latency_clock_origin.json'
                      if run.get('absolute_path') else None)
            if origin is not None:
                if not origin.is_file():
                    raise ValueError(f"{run['path']}: missing active CPU provenance")
                actual = json.loads(origin.read_text())
                expected = {'timing': 'TimingSimple', 'o3': 'O3', 'kvm': 'Kvm'}[config['cpu_type']]
                cpus = actual.get('active_cpus', [])
                if len(cpus) != config['num_cores'] or any(expected not in cpu for cpu in cpus):
                    raise ValueError(f"{run['path']}: active CPU model mismatch: {cpus}")
        for stream, metrics in run['metrics'].items():
            published, received = metrics['published'], metrics['received']
            if published == 0:
                raise ValueError(f"{run['path']}: {stream} had no source publications")
            row = dict(variant=run['variant'], case=run['path'], mode=config['mode'],
                       cpu=config.get('cpu_type') if config['mode'] == 'chimaera' else 'native',
                       stream=stream, published=published, received=received,
                       delivery_fraction=metrics['delivery_fraction'],
                       actual_source_hz=metrics['actual_source_hz'],
                       mean_ms=metrics['latency'].get('mean_ms'),
                       p95_ms=metrics['latency'].get('p95_ms'),
                       p99_ms=metrics['latency'].get('p99_ms'),
                       max_ms=metrics['latency'].get('max_ms'),
                       age_mean_ms=metrics.get('age', {}).get('mean_ms'),
                       age_p99_ms=metrics.get('age', {}).get('p99_ms'),
                       duplicates=metrics.get('duplicates'), reordered=metrics.get('reordered'),
                       age_max_ms=metrics.get('age', {}).get('max_ms'),
                       missing_at_drain=metrics.get('missing_at_drain'),
                       transport_backend=config.get('transport_backend', 'm5'),
                       delivery_policy=config.get('delivery_policy', 'latest'),
                       release_lag_mean_ms=metrics['release_lag'].get('mean_ms'),
                       wall_seconds=config['wall_seconds'])
            for mode, control in controls.items():
                native = control['metrics'][stream]
                if native['published'] == 0 or native['received'] == 0:
                    raise ValueError(f"{control['path']}: unusable native control for {stream}")
                row[mode + '_case'] = control['path']
                row[mode + '_mean_ms'] = native['latency']['mean_ms']
                row[mode + '_delivery_fraction'] = native['delivery_fraction']
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    runs = collect(args.root)
    if not runs:
        parser.error('No completed run summaries found')
    for run in runs:
        run['absolute_path'] = str((args.root / run['path']).resolve())
    rows = summarize(runs)
    for run in runs:
        del run['absolute_path']
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'runs.json').write_text(json.dumps(runs, indent=2) + '\n')
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (args.output / 'comparison.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f'{len(runs)} runs, {len(rows)} stream measurements; matched native controls verified')


if __name__ == '__main__':
    main()
