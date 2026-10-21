#!/usr/bin/env python3
"""Add host-only UDP baselines without changing the matched SHM/gem5 experiment."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge'))
from chimaera_ros.dds import udp_loopback_profile
from middleware.report import CPU_FIELDS, MATCH_FIELDS, fingerprints
from middleware.benchmark import snapshot

NATIVE_MODES = ('native_free', 'native_stepped')
DDS_FIELDS = ('dds_shm_bytes', 'dds_tuning', 'dds_max_message_bytes')


def native_cases(record):
    if not record.get('comparisons_validated'):
        raise ValueError('Require validated reference measurements')
    cases = {}
    for attempt in record['attempts']:
        summary = attempt.get('summary')
        if not summary or summary['config']['mode'] not in NATIVE_MODES:
            continue
        config = summary['config']
        key = (config['workload'], config['mode'])
        if attempt['result'] != 'complete' or key in cases:
            raise ValueError('Incomplete or ambiguous native reference')
        cases[key] = attempt
    if not cases or any((workload, mode) not in cases for workload, _ in cases for mode in NATIVE_MODES):
        raise ValueError('Require both native clock modes for each workload')
    return cases


def metrics(summary):
    result = dict(summary.get('streams') or summary['directions'])
    if 'stereo_pairs' in summary:
        result['stereo_pair'] = summary['stereo_pairs']
    return result


def delivered(metric):
    return (metric['published'] > 0 and metric['received'] == metric['published'] and
            not any(metric.get(key, 0) for key in ('missing_at_drain', 'duplicates', 'reordered')))


def compare(reference, record):
    before = native_cases(reference)
    if record['physical_host'] != reference['physical_host']:
        raise ValueError('Physical host differs from the SHM reference')
    after = {}
    for attempt in record['attempts']:
        if attempt['result'] != 'complete':
            raise ValueError('Incomplete UDP baseline')
        config = attempt['summary']['config']
        key = config['workload'], config['mode']
        if key in after:
            raise ValueError('Ambiguous UDP baseline')
        after[key] = attempt
    if before.keys() != after.keys():
        raise ValueError('UDP baseline cohort differs from reference')
    rows = []
    expected_xml = hashlib.sha256(udp_loopback_profile().encode()).hexdigest()
    for key, original in sorted(before.items()):
        current = after[key]
        a, b = [attempt['summary']['config'] for attempt in (original, current)]
        if a.get('dds_tuning') != 'large_shm_only' or not a.get('dds_shm_bytes') or (
                b.get('dds_tuning') != 'udp_loopback' or b.get('dds_shm_bytes') != 0 or
                b.get('dds_max_message_bytes') != 65500):
            raise ValueError('Require SHM-only versus UDP-loopback-only profiles')
        fields = [field for field in (*MATCH_FIELDS, *CPU_FIELDS) if field not in DDS_FIELDS]
        if any(a.get(field) != b.get(field) for field in fields):
            raise ValueError('Native workload or clock settings differ')
        hashes = [fingerprints(config) for config in (a, b)]
        if hashes[1].pop('dds.xml', None) != expected_xml:
            raise ValueError('UDP XML does not disable shared memory')
        if not hashes[0].pop('dds.xml', None) or hashes[0] != hashes[1] or not hashes[0]:
            raise ValueError('Native runtime fingerprints differ')
        old, new = [metrics(attempt['summary']) for attempt in (original, current)]
        if old.keys() != new.keys():
            raise ValueError('Native stream sets differ')
        valid = all(delivered(m) for cohort in (old, new) for m in cohort.values())
        for stream, shm in old.items():
            udp = new[stream]
            equal = shm['published'] == udp['published']
            baseline, observed = shm['latency']['mean_ms'], udp['latency']['mean_ms']
            ratio = (observed / baseline if valid and equal and baseline and baseline > 0 else None)
            rows.append(dict(workload=key[0], mode=key[1], stream=stream,
                delivery_valid=valid, equal_source_count=equal,
                mean_increase_ms=observed-baseline if valid and equal else None,
                udp_to_shm_mean_ratio=ratio, metrics=dict(shm=shm, udp=udp)))
    return dict(physical_host=record['physical_host'], rows=rows,
        interpretation='Host-only UDP loopback versus the retained SHM-only native references. '
            'Native timestamps use 1 ms buckets; a zero mean cannot define a ratio. DDS packet '
            'limits differ (65500 bytes UDP versus 1 MiB SHM), so this compares complete DDS '
            'transport configurations, not isolated SHM copy cost. Neither baseline uses gem5.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--physical-host', required=True)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    reference_bytes = args.reference.read_bytes()
    reference = json.loads(reference_bytes)
    cases = native_cases(reference)
    if reference['physical_host'] != args.physical_host:
        raise ValueError('Run on the same physical host as the SHM references')
    output = args.output.resolve()
    record_path = output / 'native-attempts.json'
    if args.report_only:
        record = json.loads(record_path.read_text())
    else:
        from chimaera_ros import latency
        output.mkdir(parents=True, exist_ok=False)
        source = snapshot(output)
        record = dict(physical_host=args.physical_host, attempts=[],
            reference_sha256=hashlib.sha256(reference_bytes).hexdigest(),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            host_identity_note='Physical host supplied by operator; container hostnames differ.')
        environment = dict(os.environ, CHIMAERA_PROFILE='0')
        for name in list(environment):
            if name.startswith('CHIMAERA_DDS_'):
                environment.pop(name)
        for (workload, mode), original in sorted(cases.items()):
            config = original['summary']['config']
            inputs = (*MATCH_FIELDS, *CPU_FIELDS, 'mode', 'repeat')
            config = {key: config[key] for key in inputs if key in config}
            config.update(dds_tuning='udp_loopback', dds_shm_bytes=0,
                dds_max_message_bytes=65500, timeout=args.timeout)
            name = workload + '-' + mode + '-udp'
            directory = output / name
            attempt = dict(case=name, result='failed', provenance=dict(
                host=platform.node(), loadavg=os.getloadavg(), native_only=True,
                environment=dict(ROS_LOCALHOST_ONLY='0', RMW_IMPLEMENTATION='rmw_fastrtps_cpp',
                    RMW_FASTRTPS_USE_QOS_FROM_XML='0'), dds_transport='UDPv4 loopback only',
                    shared_memory=False, data_sharing=False))
            print('START', name, flush=True)
            try:
                unused = Path('/unused-native-artifact')
                latency.run_case(directory, config, args.runtime.resolve(), unused, unused,
                    unused, environment=environment)
                attempt.update(result='complete', summary=json.loads((directory/'summary.json').read_text()))
            except Exception as error:
                traceback.print_exc()
                attempt['error'] = str(error)
            record['attempts'].append(attempt)
            record_path.write_text(json.dumps(record, indent=2) + '\n')
            print('FINISH', name, attempt['result'], flush=True)
    if record['reference_sha256'] != hashlib.sha256(reference_bytes).hexdigest():
        raise ValueError('Reference evidence changed')
    result = compare(reference, record)
    result['source_sha256'] = hashlib.sha256(record_path.read_bytes()).hexdigest()
    (output/'native-comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    rows = []
    for row in result['rows']:
        flat = {key: value for key, value in row.items() if key != 'metrics'}
        for transport, metric in row['metrics'].items():
            for stat in ('mean_ms', 'p95_ms', 'p99_ms', 'max_ms'):
                flat[transport+'_'+stat] = metric['latency'].get(stat)
            flat[transport+'_published'] = metric['published']
            flat[transport+'_received'] = metric['received']
            flat[transport+'_age_mean_ms'] = metric['age']['mean_ms']
        rows.append(flat)
    with (output/'native-comparison.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    print(len(rows), 'native stream/pair comparisons', flush=True)
    return 0 if all(row['delivery_valid'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
