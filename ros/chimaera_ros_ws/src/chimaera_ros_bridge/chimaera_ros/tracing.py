"""Reusable Chimaera trace analysis. No application message parsing is required."""
import argparse
import json
import math
from pathlib import Path
import statistics
import shutil


def restore_prefix(checkpoint, destination):
    """Preserve exported guest file offsets when restoring process memory."""
    target = Path(destination) / 'gem5'
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted((Path(checkpoint) / 'gem5').glob('chimaera-trace-guest-*.jsonl')):
        shutil.copy2(path, target / path.name)


def statistics_ms(values):
    values = sorted(values)
    if not values:
        return None
    return dict(samples=len(values), minimum_ms=values[0], median_ms=statistics.median(values),
                mean_ms=statistics.mean(values), maximum_ms=values[-1],
                p95_ms=values[math.ceil(.95 * len(values)) - 1],
                p99_ms=values[math.ceil(.99 * len(values)) - 1])


def summarize(directory, routes, start=0., end=None):
    """Join DDS identities or bridge envelope identities across host/guest.

    Select source publications in [start, end], retaining only receipts by end.
    Missing receipts are censored at evaluation, never assigned zero latency.
    RMW take includes deserialization; it precedes the application callback.
    """
    if not math.isfinite(start) or start < 0 or (end is not None and (
            not math.isfinite(end) or end < start)):
        raise ValueError('Invalid trace measurement window')
    directory = Path(directory)
    paths = sorted({*directory.glob('chimaera-trace-*.jsonl'),
                    *(directory / 'gem5').glob('chimaera-trace-*.jsonl')})
    result = dict(schema_version=1, status='not_recorded', clock='co-simulation', streams=[],
                  files=[str(path.relative_to(directory)) for path in paths],
                  start_seconds=start, end_seconds=end,
                  scope='Source publication to receiving RMW take (Fast DDS), or bridge ingress '
                        'to destination publication (serialized bridge). Simulation time; '
                        'host timestamps use coordinator boundary resolution. Missing receipts '
                        'are censored at evaluation. The first take per source sample defines '
                        'latency; additional duplicate/fanout takes are counted separately. '
                        'Instrumentation adds clock/export work.')
    if not paths:
        return result
    if isinstance(routes, (str, Path)):
        routes = json.loads(Path(routes).read_text())
    aliases, selected = {}, {}
    for route in routes['topics']:
        topic = route['host_topic']
        if topic in selected:
            raise ValueError('Ambiguous trace route: ' + topic)
        selected[topic] = route['direction']
        for alias in (topic, route['guest_topic']):
            if alias in aliases and aliases[alias] != topic:
                raise ValueError('Ambiguous trace alias: ' + alias)
            aliases[alias] = topic
    events = []
    for path in paths:
        with path.open() as source:
            for number, line in enumerate(source, 1):
                try:
                    event = json.loads(line)
                    if (event['version'] != 1 or event['event'] not in ('publish', 'take')
                            or event['side'] not in ('host', 'guest')
                            or event['boundary'] not in ('bridge', 'rmw')
                            or not isinstance(event['id'], str) or not event['id']
                            or (event['time_ns'] is not None and (
                                type(event['time_ns']) is not int or event['time_ns'] < 0))):
                        raise ValueError('Invalid trace event')
                    if event['topic'] in aliases:
                        events.append(dict(event, topic=aliases[event['topic']]))
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError(f'{path}:{number}: invalid or truncated trace record') from error
    lower, upper = start * 1e9, end * 1e9 if end is not None else math.inf
    for topic, direction in selected.items():
        source_side = 'host' if direction == 'host_to_guest' else 'guest'
        all_rows = [r for r in events if r['topic'] == topic]
        # Bridge envelopes explicitly span the cross-simulator hop. RMW samples
        # in a serialized bridge session describe only its local DDS hops.
        boundary = 'bridge' if any(r['boundary'] == 'bridge' for r in all_rows) else 'rmw'
        rows = [r for r in all_rows if r['boundary'] == boundary]
        published, received = {}, {}
        invalid = sum(r['time_ns'] is None for r in rows)
        for row in rows:
            if row['event'] == 'publish' and row['side'] == source_side:
                if row['id'] in published:
                    raise ValueError('Duplicate source trace identity for ' + topic)
                published[row['id']] = row
            elif row['event'] == 'take' and row['side'] != source_side:
                received.setdefault(row['id'], []).append(row)
        window = {key: row for key, row in published.items()
                  if row['time_ns'] is not None and lower <= row['time_ns'] <= upper}
        samples, duplicates, negative, missing = [], 0, 0, 0
        for key, publication in window.items():
            receipts = sorted(r['time_ns'] for r in received.get(key, [])
                              if r['time_ns'] is not None and r['time_ns'] <= upper)
            if not receipts:
                missing += 1
                continue
            duplicates += len(receipts) - 1
            latency = receipts[0] - publication['time_ns']
            if latency < 0:
                negative += 1
                continue
            samples.append(dict(publish_seconds=publication['time_ns'] / 1e9,
                                receive_seconds=receipts[0] / 1e9, latency_ms=latency / 1e6))
        samples.sort(key=lambda sample: sample['publish_seconds'])
        orphan = sum(key not in published for key in received)
        status = ('invalid_clock' if negative else 'partial' if invalid or orphan else
                  'recorded' if window else 'no_publications')
        stride = max(1, math.ceil(len(samples) / 2000))
        result['streams'].append(dict(topic=topic, direction=direction, boundary=boundary,
            status=status, published=len(window), received=len(samples),
            missing_at_evaluation=missing, duplicates=duplicates,
            invalid_clock_events=invalid, negative_latency=negative,
            unmatched_receipt_identities=orphan,
            latency=statistics_ms([sample['latency_ms'] for sample in samples]),
            time_trace=samples[::stride],
            time_trace_stride=stride))
    result['status'] = ('invalid_clock' if any(s['negative_latency'] for s in result['streams']) else
                        'partial' if any(s['status'] == 'partial' for s in result['streams']) else
                        'recorded' if any(s['published'] for s in result['streams']) else 'no_publications')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--routes', type=Path, required=True)
    parser.add_argument('--start', type=float, default=0.)
    parser.add_argument('--end', type=float)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = summarize(args.directory, args.routes, args.start, args.end)
    text = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
