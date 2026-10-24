#!/usr/bin/env python3
"""Recompute the retained overnight findings without running a simulation."""
import collections
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import tarfile


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    return dict(samples=len(values), minimum_ms=min(values),
                median_ms=statistics.median(values), maximum_ms=max(values))


def analyze(directory):
    snapshot = json.loads((directory / 'snapshot.json').read_text())
    archive_path = directory / 'evidence.tar.gz'
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == snapshot['evidence_sha256']
    checkpoint = snapshot['checkpoint']
    saved_seconds = checkpoint['saved_tick'] / 1e12 - checkpoint['time_origin_ns'] / 1e9
    findings = []
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()

        def read(name):
            stream = archive.extractfile(name) if name in names else None
            return stream.read().decode() if stream else None

        def read_json(name):
            content = read(name)
            return json.loads(content) if content else None

        for case in snapshot['cases']:
            attempt = case['attempts'][-1]
            prefix = f"attempts/{attempt['id']}/suite/{case['id']}-attempt-001/"
            journal = read(prefix + 'guest_records.jsonl')
            finding = dict(case=case['id'], status=case['status'], attempt=attempt['id'],
                           machine=attempt['machine'], collected=bool(journal))
            findings.append(finding)
            if journal is None:
                continue  # Still-running attempts were not collected in this snapshot.
            records = collections.defaultdict(list)
            for line in journal.splitlines():
                envelope = json.loads(line)
                records[envelope['stream']].append(envelope['record'])
            result = read_json(prefix + 'result.json')
            finding['result'] = result
            finding['reconnect'] = read_json(prefix + 'checkpoint-reconnect.json')
            post_save = [r for r in records['perception'] if r['capture_time'] > saved_seconds]
            finding['post_save_perception'] = dict(collections.Counter(
                r.get('status', 'detected') for r in post_save))
            if result:
                start, end = result['launch_time'], result['result_time']
                pairs = [r for r in records['perception']
                         if start <= r['capture_time'] <= end and r['completed_time'] <= end]
                plans = [r for r in records['interception']
                         if start <= r['time'] <= end and r['completed_time'] <= end]
                assert abs(end - start - result['duration']) < 1e-8
            else:
                pairs, plans = post_save, []
            finding['perception_counts'] = dict(collections.Counter(
                r.get('status', 'detected') for r in pairs))
            finding['tracked'] = sum(r.get('state') is not None for r in pairs)
            finding['plans'] = plans
            for label, values in (
                ('capture_to_pair', [(r['receive_time'] - r['capture_time']) * 1000 for r in pairs]),
                ('capture_to_completion', [(r['completed_time'] - r['capture_time']) * 1000 for r in pairs]),
                ('detection', [r['detection_simulation_seconds'] * 1000 for r in pairs
                               if isinstance(r.get('detection_simulation_seconds'), (int, float))]),
            ):
                finding[label] = distribution(values)
            trace = read_json(prefix + 'transport_latency.json')
            if trace:
                finding['rmw_streams'] = [{k: stream[k] for k in
                    ('topic', 'direction', 'published', 'received', 'missing_at_evaluation', 'latency')}
                    for stream in trace['streams'] if stream['boundary'] == 'rmw']
            finding['trajectory_publications'] = 0
            for name in names:
                if name.startswith(prefix) and 'chimaera-trace-guest-' in name:
                    for line in read(name).splitlines():
                        event = json.loads(line)
                        finding['trajectory_publications'] += int(
                            event['topic'] == '/robot/joint_trajectory' and event['event'] == 'publish')
            stats = read(prefix + 'gem5/stats.txt')
            counters = {}
            for line in (stats or '').splitlines():
                fields = line.split()
                if len(fields) >= 2 and '.functionalCopies.' in fields[0]:
                    counters[fields[0].rsplit('.', 1)[-1]] = int(fields[1])
            finding['functional_copy_counters_last_dump'] = counters
            finding['telemetry'] = read_json(prefix + 'guest_telemetry.json')
            finding['launch_validation'] = read_json(prefix + 'launch_validation.json')
    return dict(snapshot_utc=datetime.datetime.fromtimestamp(
                    snapshot['snapshot_unix'], datetime.timezone.utc).isoformat(),
                experiment_id=snapshot['experiment_id'], saved_application_seconds=saved_seconds,
                freshness_budget_ms=1000 * snapshot['config']['fixed']['image_age_periods'] /
                                    snapshot['config']['rates'][0]['camera_hz'],
                cases=findings)


if __name__ == '__main__':
    directory = Path(__file__).resolve().parent
    summary = analyze(directory)
    destination = directory / 'summary.json'
    destination.write_text(json.dumps(summary, indent=2) + '\n')
    for case in summary['cases']:
        print(case['case'], case['status'], case.get('perception_counts'),
              'trajectory_publications=', case.get('trajectory_publications'))
