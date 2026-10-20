#!/usr/bin/env python3
"""Measure trace intervals without confusing payload DMA with ROS callback delay."""
import argparse
import csv
import json
import re
from pathlib import Path


DMA = re.compile(r'(\d+): .*: (RX|TX) (HEADER|PAYLOAD_BEGIN|COMPLETE) cookie=(\d+) bytes=(\d+)')
IRQ = re.compile(r'(\d+): .*: IRQ causes=(0x[0-9a-f]+|0) ')
IMAGE = re.compile(r'CHIMAERA_IMAGE component=bridge stage=(\w+) capture_ns=(\d+) now_ns=(\d+)')


def dma_intervals(lines, ticks_per_ns=1000):
    pending, awaiting_ack, records = {}, {}, []
    causes = 0
    for line in lines:
        irq = IRQ.search(line)
        if irq:
            tick, next_causes = int(irq[1]), int(irq[2], 0)
            for direction, bit in (('RX', 1), ('TX', 2)):
                if causes & bit and not next_causes & bit and direction in awaiting_ack:
                    transfer = awaiting_ack.pop(direction)
                    transfer['irq_ack_tick'] = tick
                    transfer['completion_to_ack_ns'] = (tick - transfer['complete_tick']) / ticks_per_ns
            causes = next_causes
            continue
        match = DMA.search(line)
        if not match:
            continue
        tick, direction, phase, cookie, size = match.groups()
        key = direction, int(cookie)
        if phase == 'HEADER':
            pending[key] = dict(direction=direction, cookie=int(cookie), header_tick=int(tick))
        elif key in pending:
            transfer = pending[key]
            if phase == 'PAYLOAD_BEGIN':
                transfer.update(payload_tick=int(tick), bytes=int(size))
            elif 'payload_tick' in transfer:
                transfer.update(complete_tick=int(tick),
                                header_to_payload_ns=(transfer['payload_tick'] - transfer['header_tick']) / ticks_per_ns,
                                payload_ns=(int(tick) - transfer['payload_tick']) / ticks_per_ns)
                if transfer['header_to_payload_ns'] < 0 or transfer['payload_ns'] < 0:
                    raise ValueError('Nonmonotonic DMA trace')
                records.append(transfer)
                awaiting_ack[direction] = transfer
                del pending[key]
    return records


def image_fifo_join(transfers, events):
    """Infer the image identity only for a complete, ordered one-to-one trace."""
    source, publications = {}, {}
    for stream in ('left_image', 'right_image'):
        sent = [e for e in events if e['side'] == 'host' and e['event'] == 'send' and e['stream'] == stream]
        arrived = [e for e in events if e['side'] == 'guest' and e['event'] == 'receive' and e['stream'] == stream]
        captures = [int(e['scheduled_ns']) for e in sent]
        received = [int(e['scheduled_ns']) for e in arrived]
        if not captures or len(set(captures)) != len(captures) or captures != sorted(captures):
            return {}, 'Source captures are empty, duplicated or unordered'
        if received != captures:
            return {}, 'A camera has a missing, duplicated or reordered receipt'
        source[stream] = captures
        publications[stream] = {int(e['scheduled_ns']): e for e in sent}
    if source['left_image'] != source['right_image']:
        return {}, 'Source camera captures do not form complete stereo pairs'
    bulk = [t for t in transfers if t['direction'] == 'RX' and t['bytes'] > 1024 * 1024]
    if len(bulk) != len(source['left_image']) or len({t['bytes'] for t in bulk}) != 1:
        return {}, 'Bulk RX count/size does not match the complete image source'
    return {capture: dict(transfer=transfer, publish_ns=min(
                int(publications[side][capture]['publish_ns']) for side in publications))
            for capture, transfer in zip(source['left_image'], bulk)}, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ticks-per-ns', type=float, default=1000)
    args = parser.parse_args()
    config = json.loads((args.case / 'run.json').read_text())
    origin = json.loads((args.case / 'gem5/latency_clock_origin.json').read_text())
    with (args.case / 'gem5/dma-debug.log').open() as source:
        transfers = dma_intervals(source, args.ticks_per_ns)
    start = round(config['warmup'] * 1e9)
    stop = start + round(config['duration'] * 1e9)
    stages = {}
    trace_files = list((args.case / 'gem5').glob('*.device'))
    exported = args.case / 'gem5/chimaera-image-trace.log'
    if exported.is_file():
        trace_files.append(exported)
    for path in trace_files:
        for match in IMAGE.finditer(path.read_text(errors='replace')):
            phase, capture, now = match.groups()
            if start <= int(capture) < stop:
                stages.setdefault(int(capture), {})[phase] = int(now)
    with (args.case / 'events.csv').open() as stream:
        events = list(csv.DictReader(stream))
    received = {}
    for event in events:
        if event['side'] == 'guest' and event['event'] == 'receive' and event['stream'] in ('left_image', 'right_image'):
            received.setdefault(int(event['scheduled_ns']), {})[event['stream']] = int(event['callback_ns'])
    joined, join_error = image_fifo_join(transfers, events)
    samples = []
    for capture, stage in sorted(stages.items()):
        sample = dict(capture_ns=capture, stages=stage)
        for name, begin, end in [('bridge_view_setup_ns', 'received', 'publish_ready'),
                                 ('bridge_ros_publish_ns', 'publish_ready', 'published')]:
            if begin in stage and end in stage:
                sample[name] = stage[end] - stage[begin]
        pair = received.get(capture, {})
        if len(pair) == 2 and 'published' in stage:
            # DDS can dispatch a callback on the other core before publish returns.
            sample['publish_return_to_last_callback_ns'] = max(pair.values()) - stage['published']
            sample['capture_to_last_callback_ns'] = max(pair.values()) - capture
        if capture in joined:
            value = joined[capture]
            transfer, published = value['transfer'], value['publish_ns']
            sample.update(source_publish_ns=published, source_release_lag_ns=published - capture,
                          publication_to_descriptor_ready_ns=transfer['header_tick'] / args.ticks_per_ns - origin['origin_ns'] - published,
                          descriptor_to_payload_ns=transfer['header_to_payload_ns'],
                          dma_payload_ns=transfer['payload_ns'])
            if 'irq_ack_tick' in transfer and 'received' in stage:
                sample.update(completion_to_irq_ack_ns=transfer['completion_to_ack_ns'],
                              irq_ack_to_bridge_receive_ns=stage['received'] -
                              (transfer['irq_ack_tick'] / args.ticks_per_ns - origin['origin_ns']))
            phases = ('publication_to_descriptor_ready_ns', 'descriptor_to_payload_ns',
                      'dma_payload_ns', 'completion_to_irq_ack_ns', 'irq_ack_to_bridge_receive_ns',
                      'bridge_view_setup_ns', 'bridge_ros_publish_ns', 'publish_return_to_last_callback_ns')
            if any(sample[phase] < -2 for phase in phases[:-1] if phase in sample):
                raise ValueError('FIFO inference produced a negative causal interval')
            if len(pair) == 2 and all(phase in sample for phase in phases):
                direct = max(pair.values()) - published
                sample.update(publication_to_last_callback_ns=direct,
                              decomposition_residual_ns=direct - sum(sample[phase] for phase in phases))
                if abs(sample['decomposition_residual_ns']) > 2:
                    raise ValueError('Trace decomposition does not reconstruct callback latency')
        samples.append(sample)
    result = dict(case=str(args.case), config=config, cpu_origin=origin,
                  diagnostic_only=True, ticks_per_ns=args.ticks_per_ns,
                  trace_files=[str(p.relative_to(args.case)) for p in trace_files],
                  join_method='FIFO inference, gated on complete ordered camera receipts and matching bulk DMA count/size',
                  join_refused=join_error,
                  notes=[
                      'HEADER is after descriptor-header fetch; earlier queue/descriptor time is excluded.',
                      'Header-to-payload includes SG setup and possible TX mailbox backpressure.',
                      'Payload interval includes coherent memory timing until all responses complete.',
                      'DMA records cover the whole ROI; bridge samples cover scheduled captures in the measurement window.',
                      'Bridge traces are throttled and add guest work. Capture-to-bridge receipt combines boundary wait, DMA, IRQ and scheduling.',
                      'Device traces have no application ID. Cross-trace intervals use FIFO inference only after one-to-one source/receipt and bulk-count checks; otherwise they remain separate.',
                      'Completion-to-ACK includes IRQ delivery and kernel completion handling. ACK-to-bridge receipt includes worker wakeup, scheduling and protocol parsing.',
                      'Publish return can follow a callback on the other core; negative return-to-callback intervals represent overlap.'],
                  dma=transfers, bridge_samples=samples)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(f'{len(transfers)} DMA intervals, {len(samples)} traced measured captures')


if __name__ == '__main__':
    main()
