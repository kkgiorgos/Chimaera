#!/usr/bin/env python3
"""Join explicit image IDs through the DDS pipeline and check attribution sums."""
import argparse
import collections
import csv
import json
import math
from pathlib import Path
import statistics

from profile_integrated import dma_intervals


def stats(values):
    values = sorted(values)
    if not values:
        return None
    return dict(count=len(values), mean_ms=statistics.mean(values) / 1e6,
                p50_ms=values[math.ceil(.5 * len(values)) - 1] / 1e6,
                p95_ms=values[math.ceil(.95 * len(values)) - 1] / 1e6,
                max_ms=values[-1] / 1e6, min_ms=values[0] / 1e6)


def read_trace(case):
    rows = []
    for path in sorted(case.glob('**/pipeline-*.csv')):
        with path.open() as handle:
            records = [{k: v if k == 'stage' else int(v) for k, v in r.items()}
                       for r in csv.DictReader(handle)]
        # Native controls have both endpoints on the host, so use their actual
        # source/consumer role instead of pretending their clocks cross a VM.
        side = 'guest' if any(r['stage'] == 'callback_begin' for r in records) else 'host'
        if path.parent.name == 'gem5':
            side = 'guest'
        for row in records:
            row.update(side=side, file=str(path.relative_to(case)))
        rows.extend(records)
    return rows


STAGES = [
    ('publish_to_serialize', 'publish_entry', 'serialize_begin'),
    ('serialization', 'serialize_begin', 'serialize_end'),
    ('dds_send_and_packet_allocation', 'serialize_end', 'transport_send_begin'),
    ('host_ipc_and_router_parse', 'transport_send_begin', 'router_accept'),
    ('host_router_boundary_wait', 'router_accept', 'router_take'),
    ('host_controller_and_mailbox', 'router_take', 'mailbox_commit'),
    ('admission_to_dma_header', 'mailbox_commit', 'dma_header'),
    ('dma_descriptor_setup', 'dma_header', 'dma_payload_begin'),
    ('dma_payload', 'dma_payload_begin', 'dma_complete'),
    ('irq_driver_and_completion_thread', 'dma_complete', 'dma_completion_read'),
    ('guest_bridge_scheduling', 'dma_completion_read', 'dma_guest_service'),
    ('guest_decode_and_bridge_exchange', 'dma_guest_service', 'router_receive'),
    ('guest_router_and_ipc', 'router_receive', 'transport_receive'),
    ('dds_reader_history_and_executor_wait', 'transport_receive', 'deserialize_begin'),
    ('deserialization', 'deserialize_begin', 'deserialize_end'),
    ('rclcpp_to_callback', 'deserialize_end', 'callback_entry'),
]


def analyze(case):
    run = json.loads((case / 'run.json').read_text())
    rows = read_trace(case)
    by_image = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in rows:
        by_image[row['stream'], row['capture_ns']][row['stage']].append(row)
    with (case / 'events.csv').open() as handle:
        for event in csv.DictReader(handle):
            if event['stream'] not in ('left_image', 'right_image'):
                continue
            stream = 1 if event['stream'] == 'left_image' else 2
            if event['side'] == 'guest' and event['event'] == 'receive':
                by_image[stream, int(event['scheduled_ns'])]['callback_entry'].append(
                    dict(stage='callback_entry', sim_ns=int(event['callback_ns'])))
            if event['side'] == 'host' and event['event'] == 'send':
                by_image[stream, int(event['scheduled_ns'])]['publish_entry'].append(
                    dict(stage='publish_entry', sim_ns=int(event['publish_ns'])))
    dma_errors = []
    debug = case / 'gem5/dma-debug.log'
    if debug.is_file():
        origin = json.loads((case / 'gem5/latency_clock_origin.json').read_text())['origin_ns']
        transfers = [t for t in dma_intervals(debug.read_text().splitlines())
                     if t['direction'] == 'RX' and t['bytes'] > 100000]
        receipts = sorted([r for r in rows if r['stage'] == 'dma_completion_read'],
                          key=lambda r: r['sim_ns'])
        if len(transfers) != len(receipts):
            dma_errors.append(f'Bulk DMA count {len(transfers)} != traced completions {len(receipts)}')
        else:
            for transfer, receipt in zip(transfers, receipts):
                times = {s: transfer[k] / 1000 - origin for s, k in (
                    ('dma_header', 'header_tick'), ('dma_payload_begin', 'payload_tick'),
                    ('dma_complete', 'complete_tick'))}
                if transfer['bytes'] != receipt['bytes'] or times['dma_complete'] > receipt['sim_ns']:
                    dma_errors.append(f'FIFO DMA identity check failed for {receipt}')
                    continue
                image = by_image[receipt['stream'], receipt['capture_ns']]
                if len(image['dma_completion_read']) != 1:
                    dma_errors.append('Duplicate DMA completion image identity')
                    continue
                for stage, sim in times.items():
                    image[stage].append(dict(receipt, stage=stage, sim_ns=sim))
                if 'irq_ack_tick' in transfer:
                    image['dma_irq_ack'].append(dict(receipt, stage='dma_irq_ack',
                        sim_ns=transfer['irq_ack_tick'] / 1000 - origin))
    start = round(run['warmup'] * 1e9)
    stop = round((run['warmup'] + run['duration']) * 1e9)
    samples, errors = [], []
    native = run['mode'] != 'chimaera'
    for (stream, capture), stages in sorted(by_image.items()):
        if not start <= capture < stop:
            continue
        duplicates = {s: len(v) for s, v in stages.items() if len(v) != 1}
        if duplicates:
            errors.append(dict(stream=stream, capture_ns=capture, duplicate_stages=duplicates))
            continue
        points = {s: v[0] for s, v in stages.items()}
        required = ['publish_entry', 'publish_begin', 'serialize_begin', 'serialize_end',
                    'deserialize_begin', 'deserialize_end', 'callback_begin', 'callback_entry', 'verify_end']
        if not native:
            required += [s for _, a, b in STAGES for s in (a, b)]
        missing = sorted(set(required) - points.keys())
        if missing:
            errors.append(dict(stream=stream, capture_ns=capture, missing=missing))
            continue
        spans = STAGES if not native else [
            ('publish_to_serialize', 'publish_entry', 'serialize_begin'),
            ('serialization', 'serialize_begin', 'serialize_end'),
            ('local_dds_and_executor', 'serialize_end', 'deserialize_begin'),
            ('deserialization', 'deserialize_begin', 'deserialize_end'),
            ('rclcpp_to_callback', 'deserialize_end', 'callback_entry')]
        intervals = {name: points[b]['sim_ns'] - points[a]['sim_ns'] for name, a, b in spans}
        negative = {name: value for name, value in intervals.items() if value < -1}
        if negative:
            errors.append(dict(stream=stream, capture_ns=capture, negative_ns=negative))
            continue
        total = points['callback_entry']['sim_ns'] - points['publish_entry']['sim_ns']
        if abs(sum(intervals.values()) - total) > 1:
            raise ValueError('Pipeline intervals do not reconstruct callback delay')
        work = {}
        for name, a, b in [('publish_call', 'publish_begin', 'publish_end'),
                           ('serialize', 'serialize_begin', 'serialize_end'),
                           ('deserialize', 'deserialize_begin', 'deserialize_end'),
                           ('verify', 'callback_begin', 'verify_end')]:
            if a not in points or b not in points:
                continue
            work[name] = {clock: points[b][clock] - points[a][clock]
                          for clock in ('sim_ns', 'wall_ns', 'thread_cpu_ns')}
        details = {}
        for name, a, b in (
                ('device_complete_to_irq_ack', 'dma_complete', 'dma_irq_ack'),
                ('irq_ack_to_completion_read', 'dma_irq_ack', 'dma_completion_read'),
                ('rtps_receive_processing', 'transport_receive', 'dds_receive_end'),
                ('receive_return_to_deserialize', 'dds_receive_end', 'deserialize_begin')):
            if a in points and b in points:
                details[name] = points[b]['sim_ns'] - points[a]['sim_ns']
        samples.append(dict(stream=stream, capture_ns=capture, total_ns=total,
                            callback_recording_offset_ns=(points['callback_begin']['sim_ns'] -
                                                          points['callback_entry']['sim_ns']),
                            stages_ns=intervals, details_ns=details, work=work, points=points))
    cameras = {}
    for stream in (1, 2):
        values = [s for s in samples if s['stream'] == stream]
        cameras[str(stream)] = dict(total=stats([s['total_ns'] for s in values]),
            stages={name: stats([s['stages_ns'][name] for s in values])
                    for name in (values[0]['stages_ns'] if values else [])},
            details={name: stats([s['details_ns'][name] for s in values if name in s['details_ns']])
                     for name in sorted({key for s in values for key in s['details_ns']})},
            work={name: {clock: stats([s['work'][name][clock] for s in values])
                         for clock in ('sim_ns', 'wall_ns', 'thread_cpu_ns')}
                  for name in (values[0]['work'] if values else [])})
    summary = json.loads((case / 'summary.json').read_text())
    delivery = {name: {k: metric[k] for k in (
        'published', 'received', 'missing_at_drain', 'duplicates', 'reordered')}
        for name, metric in summary['streams'].items()}
    return dict(case=case.name, mode=run['mode'], cpu=run['cpu'],
                interval_us=run['interval_us'], verify_payload=run['verify_payload'],
                raw_trace_rows=len(rows), camera=cameras, samples=samples,
                errors=errors, dma_join_errors=dma_errors, delivery=delivery,
                worst_image=max(samples, key=lambda s: s['total_ns']) if samples else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = [analyze(p.parent) for p in sorted(args.root.glob('*/summary.json'))]
    args.output.write_text(json.dumps(dict(cases=cases), indent=2) + '\n')
    for case in cases:
        print(case['case'], 'samples', len(case['samples']), 'errors',
              len(case['errors']), 'DMA errors', case['dma_join_errors'])
        for camera, values in case['camera'].items():
            print(' ', camera, values['total'])


if __name__ == '__main__':
    main()
