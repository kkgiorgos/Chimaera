#!/usr/bin/env python3
"""Export matched KVM copy-reduction results without requiring native runs."""
import argparse
import csv
import json
from pathlib import Path
import statistics


MATCH = ('duration', 'warmup', 'drain', 'physics_step_ns', 'interval_us', 'poll_us',
         'cpu_type', 'num_cores', 'cpu_clock', 'l1d_size', 'l1i_size', 'l2_size',
         'l1_assoc', 'l2_assoc', 'transport_backend', 'middleware_transport', 'dma_module_options',
         'dds_shm_bytes', 'dds_tuning', 'dds_max_message_bytes', 'delivery_policy',
         'workload', 'ball_catching', 'reliability', 'depth', 'verify_payload')


def summarize(root):
    attempts, rows, reference, binaries = [], [], {}, {}
    for item in json.loads((root / 'progress.json').read_text()):
        directory = root / item['name']
        provenance = json.loads((directory / 'profile-provenance.json').read_text())
        attempt = dict(name=item['name'], result=item['result'], provenance=provenance)
        attempts.append(attempt)
        if item['result'] != 'complete':
            continue
        summary = json.loads((directory / 'summary.json').read_text())
        config = summary['config']
        origin = json.loads((directory / 'clock-origin.json').read_text())
        if config['cpu_type'] != 'kvm' or origin['active_cpus'] != ['X86KvmCPU'] * config['num_cores']:
            raise ValueError('Non-KVM CPU provenance: ' + item['name'])
        matched = {key: config.get(key) for key in MATCH}
        cohort = 'verified' if config.get('verify_payload') else 'performance'
        if reference.setdefault(cohort, matched) != matched:
            raise ValueError('Unmatched configuration: ' + item['name'])
        identities = {Path(name).name: digest for name, digest in config['artifact_sha256'].items()
                      if Path(name).name in ('latency_endpoint', 'libchimaera_latency_clock.so',
                          'librmw_fastrtps_cpp.so', 'librmw_fastrtps_shared_cpp.so', 'chimaera_dma.ko', 'dds.xml')}
        identities['gem5'] = provenance['gem5_sha256']
        if binaries.setdefault(cohort, identities) != identities:
            raise ValueError('Unmatched endpoint, RMW, DDS, module or simulator: ' + item['name'])
        variant = 'baseline' if provenance['case']['variant'] == 'original' else 'candidate'
        attempt.update(config=config, origin=origin, summary=summary)
        if config.get('copy_router_tests'):
            session = json.loads((directory / 'guest-session.json').read_text())
            command = session['guest']['processes'][0]['command']
            # Some gem5 serial output remains buffered at managed shutdown.
            # A completed endpoint behind this fail-fast shell guard also proves
            # the preceding test exited successfully.
            attempt['router_tests_passed'] = command[:2] == ['/bin/bash', '-ec'] and (
                'dds_router_test; echo CHIMAERA_COPY_ROUTER_TESTS_PASSED; exec "$@"' in command[2])
            if not attempt['router_tests_passed']:
                raise ValueError('Missing KVM router regression guard: ' + item['name'])
        for name, metrics in {**summary['streams'], 'stereo_pairs': summary['stereo_pairs']}.items():
            rows.append(dict(case=item['name'], variant=variant, cohort=cohort, stream=name,
                             published=metrics['published'], received=metrics['received'],
                             missing=metrics['missing_at_drain'],
                             duplicates=metrics.get('duplicates', 0),
                             reordered=metrics.get('reordered', 0),
                             mean_ms=metrics['latency']['mean_ms'],
                             p95_ms=metrics['latency']['p95_ms'],
                             max_ms=metrics['latency']['max_ms'],
                             age_mean_ms=metrics['age']['mean_ms'],
                             wall_seconds=config['wall_seconds']))
    aggregate = []
    for cohort in sorted({row['cohort'] for row in rows}):
        for stream in sorted({row['stream'] for row in rows}):
            entry = dict(cohort=cohort, stream=stream)
            for variant in ('baseline', 'candidate'):
                group = [row for row in rows if (row['cohort'], row['stream'], row['variant']) ==
                         (cohort, stream, variant)]
                if not group:
                    continue
                count = sum(row['received'] for row in group)
                entry[variant] = dict(
                    runs=len(group), published=sum(row['published'] for row in group), received=count,
                    missing=sum(row['missing'] for row in group),
                    duplicates=sum(row['duplicates'] for row in group),
                    reordered=sum(row['reordered'] for row in group),
                    mean_ms=sum((row['mean_ms'] or 0) * row['received'] for row in group) / count if count else None,
                    run_means_ms=[row['mean_ms'] for row in group],
                    run_p95_ms=[row['p95_ms'] for row in group],
                    max_ms=max((row['max_ms'] for row in group if row['max_ms'] is not None), default=None),
                    wall_mean_seconds=statistics.mean(row['wall_seconds'] for row in group))
            if all(variant in entry and entry[variant]['mean_ms'] for variant in ('baseline', 'candidate')):
                entry['change_percent'] = 100 * (entry['candidate']['mean_ms'] / entry['baseline']['mean_ms'] - 1)
            aggregate.append(entry)
    return dict(attempts=attempts, rows=rows, aggregate=aggregate,
                matched=reference, matched_binaries=binaries,
                all_delivered=bool(rows) and all(row['published'] > 0 and
                    row['received'] == row['published'] and
                    not (row['missing'] or row['duplicates'] or row['reordered']) for row in rows),
                limitations=['KVM uses atomic_noncaching memory and does not model detailed CPU costs.',
                             'Short independent runs; per-run p95 values are retained, not averaged into a pooled percentile.',
                             'Means include synchronization, scheduling, DDS and callback dispatch; copy cost is not isolated.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = summarize(args.runs)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    if report['rows']:
        with (args.output / 'comparison.csv').open('w') as handle:
            writer = csv.DictWriter(handle, list(report['rows'][0]))
            writer.writeheader()
            writer.writerows(report['rows'])
    for row in report['aggregate']:
        print(json.dumps(row))


if __name__ == '__main__':
    main()
