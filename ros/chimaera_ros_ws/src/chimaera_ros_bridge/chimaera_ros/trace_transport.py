"""Transport arrivals joined by DDS identity, independently of executor takes."""
import math


def completion(rows, total):
    """First complete fragment cover at one receiver; never combine fanout peers."""
    covered = []
    duplicates = 0
    completed = None
    for row in sorted(rows, key=lambda row: row['time_ns']):
        if row['fragment_total'] != total:
            raise ValueError('Inconsistent transport fragment totals')
        first, last = row['fragment_first'], row['fragment_first'] + row['fragment_count'] - 1
        before = sum(b - a + 1 for a, b in covered)
        merged = []
        for a, b in sorted([*covered, (first, last)]):
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = merged[-1][0], max(b, merged[-1][1])
            else:
                merged.append((a, b))
        covered = merged
        after = sum(b - a + 1 for a, b in covered)
        duplicates += row['fragment_count'] - (after - before)
        if after == total and completed is None:
            completed = row['time_ns']
    return completed, duplicates


def summarize_transport(window, received, packets, source_side, upper, statistics_ms):
    selected = [row for row in packets if row['id'] in window]
    result = dict(status='not_recorded', measurement='serialized_transport_v1',
        boundary='transport', published=0, received=0, missing_at_evaluation=0,
        without_send_observation=len(window), receive_observations=0, send_observations=0,
        duplicate_fragments=0, fanout_completions=0, invalid_clock_events=0,
        negative_latency=0, latency=None, time_trace=[], time_trace_stride=1,
        scope='First accepted serialized transport send entry to complete sample arrival '
              'before DDS delivery. Includes transport queues, transport-worker scheduling '
              'and fragment pacing; excludes source serialization, DDS receive processing, '
              'deserialization, executor waiting and application callbacks.')
    if not selected:
        return result, None, None
    result['invalid_clock_events'] = sum(row['time_ns'] is None for row in selected)
    sends, arrivals = {}, {}
    for row in selected:
        if row['time_ns'] is None or row['time_ns'] > upper:
            continue
        if row['event'] == 'send' and row['side'] == source_side:
            sends.setdefault(row['id'], []).append(row)
        elif row['event'] == 'receive' and row['side'] != source_side:
            arrivals.setdefault(row['id'], {}).setdefault(row['receiver'], []).append(row)
    samples, before, waiting = [], [], []
    for identity, rows in sends.items():
        result['published'] += 1
        result['send_observations'] += len(rows)
        started = min(row['time_ns'] for row in rows)
        total = rows[0]['fragment_total']
        # Validate source ranges as well, including retransmission/fanout sends.
        completion(rows, total)
        complete = {}
        for arrivals_at_process in arrivals.get(identity, {}).values():
            result['receive_observations'] += len(arrivals_at_process)
            time, duplicates = completion(arrivals_at_process, total)
            result['duplicate_fragments'] += duplicates
            if time is not None:
                process = arrivals_at_process[0]['process']
                complete[process] = min(time, complete.get(process, time))
        if not complete:
            result['missing_at_evaluation'] += 1
            continue
        ended = min(complete.values())
        if ended < started or started < window[identity]['time_ns']:
            result['negative_latency'] += 1
            continue
        result['received'] += 1
        result['fanout_completions'] += len(complete) - 1
        before.append((started - window[identity]['time_ns']) / 1e6)
        samples.append(dict(id=identity, publish_seconds=started / 1e9,
                            receive_seconds=ended / 1e9, latency_ms=(ended - started) / 1e6))
        # DDS fanout processes can receive and take at different times. Match
        # the same process rather than subtracting unrelated earliest events.
        takes = sorted((row for row in received.get(identity, [])
                        if row['time_ns'] is not None and row['time_ns'] <= upper
                        and row.get('process') in complete), key=lambda row: row['time_ns'])
        if takes:
            delay = takes[0]['time_ns'] - complete[takes[0]['process']]
            if delay < 0:
                result['negative_latency'] += 1
            else:
                waiting.append(delay / 1e6)
    samples.sort(key=lambda row: row['publish_seconds'])
    stride = max(1, math.ceil(len(samples) / 2000))
    result.update(without_send_observation=len(window) - len(sends),
        latency=statistics_ms([row['latency_ms'] for row in samples]),
        time_trace=samples[::stride], time_trace_stride=stride,
        status='invalid_clock' if result['negative_latency'] else
               'partial' if result['invalid_clock_events'] else
               'recorded' if sends else 'no_sends')
    return result, statistics_ms(before), statistics_ms(waiting)
