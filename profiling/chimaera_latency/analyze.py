#!/usr/bin/env python3
"""Join buffered stage traces to received messages, keeping both clock domains."""
import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path

UNSET = 2**64 - 1
STREAMS = ['synthetic', 'left_image', 'right_image', 'joint_states', 'effort_command', 'clock']
PATHS = {
    0: [('host', 'bridge_callback', 'source DDS / host executor'),
        ('host', 'controller_submit', 'host adapter / boundary queue'),
        ('guest', 'guest_batch_received', 'host handoff / polling / transport / decode'),
        ('guest', 'bridge_receive', 'guest controller delivery'),
        ('guest', 'bridge_publish_begin', 'guest adapter parse / allocation / copy'),
        (None, None, 'guest publish / DDS / application scheduling')],
    1: [('guest', 'bridge_callback', 'source DDS / guest executor wait'),
        ('guest', 'controller_submit', 'guest adapter / outgoing queue'),
        ('guest', 'guest_batch_send', 'wait for next guest poll'),
        ('host', 'bridge_receive', 'encode / m5 send / host boundary wait'),
        ('host', 'bridge_publish_begin', 'host adapter parse / allocation / copy'),
        (None, None, 'host publish / DDS / application scheduling')],
}


def statistics(values):
    values = sorted(values)
    if not values:
        return dict(count=0, mean_ms=None, p50_ms=None, p95_ms=None, max_ms=None)
    return dict(count=len(values), mean_ms=sum(values)/len(values)/1e6,
                p50_ms=values[math.ceil(len(values)*.5)-1]/1e6,
                p95_ms=values[math.ceil(len(values)*.95)-1]/1e6,
                max_ms=values[-1]/1e6)


def events(directory):
    with (directory/'events.csv').open() as handle:
        rows = [{key: value if key in ('side', 'event', 'stream') else int(value)
                 for key, value in row.items()} for row in csv.DictReader(handle)]
    for row in rows:
        row.setdefault('stream', 'synthetic')
    sends = {(r['side'], r['stream'], r['sequence']): r for r in rows if r['event'] == 'send'}
    for row in rows:
        if row['event'] == 'receive':
            source = sends[('host' if row['side'] == 'guest' else 'guest', row['stream'], row['sequence'])]
            if row['scheduled_ns'] != source['scheduled_ns']:
                raise ValueError('Source/receive identity mismatch')
            if row['publish_ns'] not in (UNSET, source['publish_ns']):
                raise ValueError('Source/receive publication mismatch')
            row['publish_ns'] = source['publish_ns']
            if row['callback_ns'] < row['publish_ns']:
                raise ValueError('Negative end-to-end latency')
    return rows


def traces(directory):
    rows = []
    for path in (directory/'profile-host.csv', directory/'gem5/chimaera-profile-guest.csv'):
        if not path.is_file():
            continue
        with path.open() as handle:
            rows.extend({key: value if key in ('side', 'stage', 'event') else int(value)
                         for key, value in row.items()} for row in csv.DictReader(handle))
    return rows


def identity(row):
    stream = STREAMS[row['stream']]
    return row['direction'], stream, row['sequence'] if stream == 'synthetic' else row['scheduled_ns']


def decompose(receipts, rows):
    lookup = defaultdict(dict)
    for row in rows:
        if row['event'] != 'I' or row['stream'] < 0:
            continue
        key = identity(row)
        stage = row['side'], row['stage']
        # A repeated receipt is not another independent timing observation.
        lookup[key].setdefault(stage, row)
    groups = defaultdict(lambda: dict(total=[], stages=defaultdict(list), complete=0,
                                     incomplete=0, nonmonotonic=0, skew=[]))
    seen = set()
    for receipt in receipts:
        direction = 0 if receipt['side'] == 'guest' else 1
        stream = receipt['stream']
        key = direction, stream, receipt['sequence'] if stream == 'synthetic' else receipt['scheduled_ns']
        if key in seen:
            continue
        seen.add(key)
        group = groups[(direction, stream)]
        path = PATHS[direction]
        if any((side, stage) not in lookup[key] for side, stage, _ in path if side):
            group['incomplete'] += 1
            continue
        points = [receipt['publish_ns']] + [lookup[key][(side, stage)]['sim_ns'] if side
                                             else receipt['callback_ns'] for side, stage, _ in path]
        differences = [end-begin for begin, end in zip(points, points[1:])]
        if any(value < 0 for value in differences):
            group['nonmonotonic'] += 1
            group['skew'].extend(value for value in differences if value < 0)
            continue
        if sum(differences) != receipt['callback_ns']-receipt['publish_ns']:
            raise AssertionError('Attribution does not close')
        group['complete'] += 1
        group['total'].append(receipt['callback_ns']-receipt['publish_ns'])
        for (_, _, label), value in zip(path, differences):
            group['stages'][label].append(value)
    return {f'{"host_to_guest" if direction == 0 else "guest_to_host"}/{stream}':
            dict(complete_paths=value['complete'], incomplete_paths=value['incomplete'],
                 nonmonotonic_paths=value['nonmonotonic'], latency=statistics(value['total']),
                 maximum_backwards_ms=-min(value['skew'])/1e6 if value['skew'] else 0.,
                 stages={name: statistics(samples) for name, samples in value['stages'].items()})
            for (direction, stream), value in groups.items()}


def scopes(rows, start, stop):
    pending, groups = {}, defaultdict(lambda: dict(os=[], sim=[], nonmonotonic=0))
    for row in rows:
        key = row['side'], row['stage'], row['id']
        if row['event'] == 'B':
            pending[key] = row
        elif row['event'] == 'E' and key in pending:
            begin = pending.pop(key)
            if not start <= begin['sim_ns'] < stop:
                continue
            group = groups[(row['side'], row['stage'])]
            if row['os_ns'] < begin['os_ns'] or row['sim_ns'] < begin['sim_ns']:
                group['nonmonotonic'] += 1
                continue
            group['os'].append(row['os_ns']-begin['os_ns'])
            group['sim'].append(row['sim_ns']-begin['sim_ns'])
    return {f'{side}/{stage}': dict(os_clock='host steady wall' if side == 'host' else 'guest OS steady',
            os=statistics(value['os']), simulation=statistics(value['sim']),
            total_os_seconds=sum(value['os'])/1e9,
            nonmonotonic_scopes=value['nonmonotonic'])
            for (side, stage), value in groups.items()}


def pipelines(source_rows, receipts, trace, cutoff):
    """Account for every actual source publication, including undelivered data."""
    observed = defaultdict(lambda: defaultdict(set))
    for row in trace:
        if row['event'] != 'I' or row['stream'] < 0 or row['sim_ns'] > cutoff:
            continue
        direction, stream, sample = identity(row)
        observed[(direction, stream)][(row['side'], row['stage'])].add(sample)
    groups = defaultdict(list)
    for row in source_rows:
        groups[(0 if row['side'] == 'host' else 1, row['stream'])].append(row)
    result = {}
    for (direction, stream), sources in groups.items():
        samples = {r['sequence'] if stream == 'synthetic' else r['scheduled_ns'] for r in sources}
        callback = {r['sequence'] if stream == 'synthetic' else r['scheduled_ns'] for r in receipts
                    if r['stream'] == stream and (r['side'] == 'guest') == (direction == 0)}
        stages = ([('host','bridge_callback'), ('host','bridge_enqueued'),
                   ('host','controller_submit'), ('host','host_worker_send'),
                   ('guest','guest_batch_received'), ('guest','bridge_receive'),
                   ('guest','bridge_stale'), ('guest','bridge_publish_begin')]
                  if direction == 0 else
                  [('guest','bridge_callback'), ('guest','bridge_enqueued'),
                   ('guest','controller_submit'), ('guest','guest_batch_send'),
                   ('host','host_worker_received'), ('host','bridge_receive'),
                   ('host','bridge_publish_begin')])
        result[f'{"host_to_guest" if direction == 0 else "guest_to_host"}/{stream}'] = dict(
            published=len(samples), application_receipts=len(samples & callback),
            release_lag=statistics([r['publish_ns']-r['scheduled_ns'] for r in sources]),
            stages={f'{side}/{stage}': len(samples & observed[(direction,stream)][(side,stage)])
                    for side,stage in stages})
    return result


def physics_observation(directory, rows, config, start, stop):
    path = directory/'physics-observer.csv'
    if not path.exists():
        return None
    with path.open() as handle:
        observed = list(csv.DictReader(handle))
    seen = {int(row['sim_ns']): int(row['os_ns']) for row in observed[1:]}
    requests, values = {}, []
    interval = config['interval_us']*1000
    for row in rows:
        if row['side'] != 'host':
            continue
        if row['stage'] == 'gazebo_request' and row['event'] == 'B':
            requests[row['sim_ns']] = row['os_ns']
        if (row['stage'] == 'gazebo_wait' and row['event'] == 'E'
                and start <= row['sim_ns']-interval < stop
                and row['sim_ns'] in seen and row['sim_ns'] in requests):
            point = seen[row['sim_ns']]
            begin = requests[row['sim_ns']]
            if begin <= point <= row['os_ns']:
                values.append((point-begin, row['os_ns']-point))
    return dict(matched_steps=len(values), observation_rows=len(observed),
                request_to_observed_final_preupdate=statistics([v[0] for v in values]),
                observed_final_preupdate_to_completion=statistics([v[1] for v in values]))


def wall_phases(rows, start, stop):
    pending, spans = {}, []
    for row in rows:
        if row['side'] != 'host':
            continue
        key = row['stage'], row['id']
        if row['event'] == 'B':
            pending[key] = row
        elif row['event'] == 'E' and key in pending:
            spans.append((pending.pop(key), row))
    names = ['host_boundary_ingress', 'gem5_phase', 'host_boundary_egress',
             'settle_before_physics', 'gazebo_request', 'gazebo_wait', 'settle_after_physics']
    whole = [(b,e) for b,e in spans if b['stage']=='whole_step' and start<=b['sim_ns']<stop]
    values, totals, skipped = defaultdict(list), [], 0
    # Sorting allows a single scan rather than comparing every step to every span.
    children = sorted([(b,e) for b,e in spans if b['stage'] in names], key=lambda v:v[0]['os_ns'])
    index = 0
    for begin, end in sorted(whole, key=lambda v:v[0]['os_ns']):
        while index<len(children) and children[index][0]['os_ns']<begin['os_ns']:
            index += 1
        selected, cursor = defaultdict(list), index
        while cursor<len(children) and children[cursor][0]['os_ns']<=end['os_ns']:
            b,e = children[cursor]
            if e['os_ns']<=end['os_ns']:
                selected[b['stage']].append(e['os_ns']-b['os_ns'])
            cursor += 1
        if any(len(selected[name]) != 1 for name in names):
            skipped += 1
            continue
        duration = end['os_ns']-begin['os_ns']
        residual = duration-sum(selected[name][0] for name in names)
        if residual < 0:
            raise ValueError('Overlapping exclusive wall phases')
        totals.append(duration)
        for name in names:
            values[name].append(selected[name][0])
        values['unassigned'].append(residual)
    return dict(steps=len(totals), incomplete_steps=skipped, whole_step=statistics(totals),
                phases={name:statistics(samples) for name,samples in values.items()})


def analyze(directory):
    config = json.loads((directory/'run.json').read_text())
    result = dict(directory=str(directory), config=config)
    if config['status'] != 'complete':
        return result
    rows = events(directory)
    start = round(config['warmup']*1e9)
    stop = start+round(config['duration']*1e9)
    receipts = [r for r in rows if r['event']=='receive' and start <= r['publish_ns'] < stop]
    trace = traces(directory)
    result['trace_rows'] = dict(Counter(row['side'] for row in trace))
    result['decomposition'] = decompose(receipts, trace)
    result['scopes'] = scopes(trace, start, stop)
    result['pipelines'] = pipelines(
        [r for r in rows if r['event']=='send' and start <= r['publish_ns'] < stop],
        receipts, trace, stop+round(config['drain']*1e9))
    result['physics_observer'] = physics_observation(directory, trace, config, start, stop)
    result['wall_phases'] = wall_phases(trace, start, stop)
    provenance = directory/'profile-provenance.json'
    result['profile_provenance'] = json.loads(provenance.read_text()) if provenance.exists() else None
    result['stage_counts'] = dict(Counter(f"{r['side']}/{r['stage']}" for r in trace if r['event']=='I'))
    result['summary'] = json.loads((directory/'summary.json').read_text())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = [analyze(path.parent) for path in sorted(args.root.glob('*/run.json'))]
    args.output.write_text(json.dumps(results, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
