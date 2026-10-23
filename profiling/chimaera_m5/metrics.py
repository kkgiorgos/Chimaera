"""Recompute delivery-time rates and censored application backlog from raw events."""
import csv
import math

from common import UNSET, streams


def statistics(values):
    if not values:
        return dict(count=0, mean_ms=None, p50_ms=None, p95_ms=None, p99_ms=None, max_ms=None)
    ordered = sorted(values)
    result = dict(count=len(ordered), mean_ms=sum(ordered) / len(ordered) / 1e6,
                  max_ms=ordered[-1] / 1e6)
    for percentile in (50, 95, 99):
        result[f'p{percentile}_ms'] = ordered[math.ceil(len(ordered) * percentile / 100) - 1] / 1e6
    return result


def read_events(path, config):
    events, sends = [], {}
    allowed = streams(config)
    with path.open() as handle:
        reader = csv.DictReader(handle)
        expected = {'side', 'event', 'sequence', 'scheduled_ns', 'publish_ns', 'callback_ns',
                    'physics_ns', 'payload_bytes', 'stream'}
        fields = set(reader.fieldnames or [])
        if not expected <= fields <= expected | {'publish_return_ns', 'creation_ns'}:
            raise ValueError('Unexpected event CSV schema')
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError('Truncated event CSV row')
            event = {key: int(value) if key not in ('side', 'event', 'stream') else value
                     for key, value in raw.items()}
            if event['side'] not in ('host', 'guest') or event['event'] not in ('send', 'receive'):
                raise ValueError('Invalid event identity')
            if event['stream'] not in allowed or any(
                    not 0 <= value <= UNSET for value in event.values() if type(value) is int):
                raise ValueError('Invalid stream or unsigned event field')
            source = allowed[event['stream']][0]
            if (event['side'] == source) != (event['event'] == 'send'):
                raise ValueError('Event direction disagrees with workload')
            if event['scheduled_ns'] == UNSET or event['payload_bytes'] == UNSET:
                raise ValueError('Unset required event field')
            if event['event'] == 'send':
                key = event['stream'], event['sequence']
                if key in sends:
                    raise ValueError('Duplicate source record')
                if not event['scheduled_ns'] <= event['publish_ns'] < UNSET:
                    raise ValueError('Early or unset publication')
                returned = event.get('publish_return_ns', UNSET)
                if returned != UNSET and returned < event['publish_ns']:
                    raise ValueError('Publish return precedes publication')
                creation = event.get('creation_ns', UNSET)
                if creation != UNSET and not event['scheduled_ns'] <= creation <= event['publish_ns']:
                    raise ValueError('Creation timestamp outside release/publication bounds')
                sends[key] = event
            events.append(event)
    cutoff = round((config['warmup'] + config['duration'] + config['drain']) * 1e9)
    for event in events:
        if event['event'] != 'receive':
            continue
        original = sends.get((event['stream'], event['sequence']))
        if original is None or any(event[key] != original[key] for key in ('scheduled_ns', 'payload_bytes')):
            raise ValueError('Receipt lacks matching source event')
        if event['publish_ns'] not in (UNSET, original['publish_ns']):
            raise ValueError('Receipt publication timestamp differs from source')
        event['publish_ns'] = original['publish_ns']
        creation = original.get('creation_ns', UNSET)
        if event.get('creation_ns', UNSET) not in (UNSET, creation):
            raise ValueError('Receipt creation timestamp differs from source')
        if creation != UNSET:
            event['creation_ns'] = creation
        if not event['publish_ns'] <= event['callback_ns'] <= cutoff:
            raise ValueError('Receipt outside publication/drain time bounds')
    return events


def expected_releases(start, stop, rate, burst=1):
    # Endpoint llround uses nearest with ties away from zero; all times are >=0.
    bound = math.ceil(stop / 1e9 * rate) + burst
    return sum(start <= math.floor((seq // burst * burst) * 1e9 / rate + .5) < stop
               for seq in range(bound))


def observation(sends, receipts, body_bytes, begin, end, bin_ns):
    """Receipts counted by callback time; outstanding means sent but not received.

    This reconstructs application outstanding work, not device/driver occupancy.
    Missing messages stay outstanding through the end, rather than disappearing.
    """
    timeline = [(e['publish_ns'], 0, seq, e) for seq, e in sends.items()]
    timeline += [(e['callback_ns'], 1, seq, e) for seq, e in receipts.items()]
    timeline.sort(key=lambda row: (row[0], row[1], row[2]))
    pending, position, samples = {}, 0, []
    boundaries = list(range(begin, end, bin_ns)) + [end]
    for boundary in boundaries:
        while position < len(timeline) and timeline[position][0] < boundary:
            _, kind, seq, event = timeline[position]
            if kind == 0:
                pending[seq] = event
            else:
                pending.pop(seq, None)
            position += 1
        samples.append(dict(at_ns=boundary, outstanding_messages=len(pending),
            outstanding_event_bytes=sum(event['payload_bytes'] for event in pending.values()),
            oldest_outstanding_age_ms=max((boundary - e['scheduled_ns'] for e in pending.values()),
                                          default=0) / 1e6))
    bins = []
    for lower, upper in zip(boundaries, boundaries[1:]):
        selected = [e for e in receipts.values() if lower <= e['callback_ns'] < upper]
        seconds = (upper - lower) / 1e9
        bins.append(dict(start_ns=lower, stop_ns=upper, seconds=seconds,
            receipts=len(selected), event_bytes=sum(e['payload_bytes'] for e in selected),
            body_bytes=len(selected) * body_bytes if body_bytes is not None else None,
            latency=statistics([e['callback_ns'] - e['publish_ns'] for e in selected]),
            age=statistics([e['callback_ns'] - e['scheduled_ns'] for e in selected])))
    selected = [e for e in receipts.values() if begin <= e['callback_ns'] < end]
    seconds = (end - begin) / 1e9
    return dict(start_ns=begin, stop_ns=end, seconds=seconds, bins=bins, backlog=samples,
        receipts=len(selected), messages_per_observation_second=len(selected) / seconds,
        event_bytes_per_observation_second=sum(e['payload_bytes'] for e in selected) / seconds,
        body_MB_per_observation_second=(len(selected) * body_bytes / seconds / 1e6
                                        if body_bytes is not None else None),
        backlog_growth_messages=samples[-1]['outstanding_messages'] - samples[0]['outstanding_messages'],
        oldest_age_growth_ms=samples[-1]['oldest_outstanding_age_ms'] - samples[0]['oldest_outstanding_age_ms'],
        max_outstanding_messages=max(s['outstanding_messages'] for s in samples),
        max_oldest_outstanding_age_ms=max(s['oldest_outstanding_age_ms'] for s in samples),
        interpretation='Application sent-but-unreceived backlog includes censored losses; '
                       'not internal queue/credit occupancy. Fixed half-open receipt intervals exclude drain.')


def analyze(events, config, rules):
    alignment = rules.get('bulk_observation_alignment', 'complete_periods')
    if alignment not in ('complete_periods', 'fixed_window'):
        raise ValueError('Unknown bulk observation alignment: ' + str(alignment))
    start = round(config['warmup'] * 1e9)
    stop = start + round(config['duration'] * 1e9)
    trim = round(rules['trim_seconds'] * 1e9)
    begin, end = start + trim, stop - trim
    parameters = config['benchmark_parameters']
    if parameters['bulk_bytes'] and alignment == 'complete_periods':
        period_ns = math.floor(parameters['bulk_burst'] * 1e9 / parameters['bulk_hz'] + .5)
        begin = (begin + period_ns - 1) // period_ns * period_ns
        end = end // period_ns * period_ns
    if end <= begin:
        raise ValueError('No interior observation interval (bulk requires complete burst periods)')
    bin_ns = round(rules['observation_bin_seconds'] * 1e9)
    if bin_ns <= 0:
        raise ValueError('Invalid observation interval')
    result, raw = {}, {}
    for name, (side, rate, body) in streams(config).items():
        sends = {e['sequence']: e for e in events if e['stream'] == name and e['event'] == 'send'}
        selected = {seq: e for seq, e in sends.items() if start <= e['publish_ns'] < stop}
        receipts, duplicates, reordered, largest = {}, 0, 0, -1
        for e in events:
            if e['stream'] != name or e['event'] != 'receive':
                continue
            seq = e['sequence']
            if seq in receipts:
                if seq in selected:
                    duplicates += 1
                continue
            receipts[seq] = e
            if seq in selected:
                reordered += seq < largest
                largest = max(largest, seq)
        delivered = {seq: e for seq, e in receipts.items() if seq in selected}
        scheduled = {seq: e for seq, e in sends.items() if start <= e['scheduled_ns'] < stop}
        burst = config['benchmark_parameters']['bulk_burst'] if name.startswith('bulk_') else 1
        expected = expected_releases(start, stop, rate, burst)
        lag = [e['publish_ns'] - e['scheduled_ns'] for e in selected.values()]
        publish_calls = [e['publish_return_ns'] - e['publish_ns'] for e in selected.values()
                         if e.get('publish_return_ns', UNSET) != UNSET]
        latencies = [e['callback_ns'] - e['publish_ns'] for e in delivered.values()]
        creation_latencies = [e['callback_ns'] - selected[seq]['creation_ns']
                              for seq, e in delivered.items()
                              if selected[seq].get('creation_ns', UNSET) != UNSET]
        preparation = [e['publish_ns'] - e['creation_ns'] for e in selected.values()
                       if e.get('creation_ns', UNSET) != UNSET]
        ages = [e['callback_ns'] - e['scheduled_ns'] for e in delivered.values()]
        deadline_ms = rules['image_age_deadline_ms'] if 'image' in name else rules['control_age_deadline_ms']
        observed = observation(sends, receipts, body, begin, end, bin_ns)
        received_in_window = [e for e in receipts.values() if begin <= e['callback_ns'] < end]
        source_window_receipts = sum(start <= e['publish_ns'] < stop for e in received_in_window)
        observed['receipt_publication_cohorts'] = dict(
            before_source_window=len(received_in_window) - source_window_receipts,
            source_window=source_window_receipts)
        observed['same_source_window_body_MB_per_observation_second'] = (
            source_window_receipts * body / observed['seconds'] / 1e6 if body is not None else None)
        missing = sorted(set(selected) - set(delivered))
        source_error = abs(len(scheduled) - expected) / expected if expected else None
        bounded = (len(observed['bins']) >= 4 and source_error is not None and
            source_error <= rules['max_relative_source_rate_error'] and
            not missing and not duplicates and not reordered and
            observed['backlog_growth_messages'] <= rules['max_backlog_growth_messages'] and
            observed['oldest_age_growth_ms'] <= rules['max_oldest_age_growth_ms'])
        result[name] = dict(published=len(selected), received=len(delivered),
            missing_at_drain=len(missing), censored_sequence_ids=missing, duplicates=duplicates,
            reordered=reordered, body_bytes=body, expected_scheduled_releases=expected,
            scheduled_releases_observed=len(scheduled), missed_source_releases=max(0, expected - len(scheduled)),
            extra_source_releases=max(0, len(scheduled) - expected), source_rate_relative_error=source_error,
            published_event_bytes=sum(e['payload_bytes'] for e in selected.values()),
            received_event_bytes=sum(e['payload_bytes'] for e in delivered.values()),
            offered_per_source_second=len(selected) / config['duration'],
            delivered_per_source_second=len(delivered) / config['duration'],
            delivered_body_MB_per_source_second=(len(delivered) * body / config['duration'] / 1e6
                                                 if body is not None else None),
            latency=statistics(latencies), age=statistics(ages), release_lag=statistics(lag),
            publish_call=statistics(publish_calls),
            creation_to_callback=statistics(creation_latencies), preparation=statistics(preparation),
            creation_boundary='Logical sample preparation start; preallocated payloads exclude '
                              'initial allocation and physical sensor capture. Unrecorded in historical runs.',
            age_deadline_ms=deadline_ms,
            received_age_deadline_misses=sum(age > deadline_ms * 1e6 for age in ages),
            missing_age_deadline_misses=sum(stop + config['drain'] * 1e9 - selected[seq]['scheduled_ns']
                                          > deadline_ms * 1e6 for seq in missing),
            p99_tail_sample_count=len(latencies) / 100,
            observation=observed, bounded_observation=bounded,
            sustainability='insufficient_repeated_window_evidence',
            native_clock_quantum_ms=config['physics_step_ns'] / 1e6 if config['mode'] != 'chimaera' else None)
        raw[name] = (selected, delivered)
    if all(eye in raw for eye in ('left_image', 'right_image')):
        left_sent, left_recv = raw['left_image']
        right_sent, right_recv = raw['right_image']
        pairs = set(left_sent) & set(right_sent)
        complete = pairs & set(left_recv) & set(right_recv)
        delays, ages, skews, completions = [], [], [], []
        for seq in sorted(complete):
            left, right = left_recv[seq], right_recv[seq]
            if left_sent[seq]['scheduled_ns'] != right_sent[seq]['scheduled_ns']:
                raise ValueError('Stereo sequence has different capture times')
            last = max(left['callback_ns'], right['callback_ns'])
            completions.append(last)
            delays.append(last - min(left['publish_ns'], right['publish_ns']))
            ages.append(last - left['scheduled_ns'])
            skews.append(abs(left['callback_ns'] - right['callback_ns']))
        result['stereo_pair'] = dict(published=len(pairs), received=len(complete),
            missing_at_drain=len(pairs - complete), unmatched_source_eyes=len(set(left_sent) ^ set(right_sent)),
            censored_sequence_ids=sorted(pairs - complete), latency=statistics(delays),
            age=statistics(ages), callback_skew=statistics(skews),
            delivered_per_source_second=len(complete) / config['duration'],
            observed_pairs_per_second=sum(begin <= time < end for time in completions) / ((end - begin) / 1e9),
            byte_accounting='Pair and eye rows overlap; never sum them.')
    return result
