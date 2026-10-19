"""Validate guest telemetry and recover the application's existing JSONL streams."""
import json
import math
import csv
from pathlib import Path

STREAMS = ('perception', 'interception', 'control', 'control_timing')


def _capture(directory):
    source = Path(directory) / 'guest_records.jsonl'
    if not source.exists():
        raise ValueError('Guest timing telemetry was not recorded')
    records = {name: [] for name in STREAMS}
    missing = {name: [] for name in STREAMS}
    expected = dict.fromkeys(STREAMS, 0)
    for line in source.read_text().splitlines():
        envelope = json.loads(line)
        name = envelope['stream']
        if name not in records:
            raise ValueError('Unknown guest telemetry stream: ' + str(name))
        sequence = envelope['sequence']
        if type(sequence) is not int or sequence < expected[name]:
            raise ValueError(f'Invalid guest telemetry sequence in {name}: '
                             f'expected at least {expected[name]}, received {sequence}')
        if sequence > expected[name]:
            missing[name].append(dict(first=expected[name], last=sequence - 1))
        expected[name] = sequence + 1
        record = envelope['record']
        completed = record.get('completed_time')
        if (record.get('timing_clock') != 'gem5 simulation'
                or type(completed) not in (int, float) or not math.isfinite(completed)):
            raise ValueError('Guest telemetry needs an authoritative gem5 completion timestamp')
        # Reject nonstandard JSON NaN/Infinity anywhere in a record.
        json.dumps(record, allow_nan=False)
        records[name].append(record)
    return records, missing


def _missing_count(missing):
    return sum(gap['last'] - gap['first'] + 1 for gaps in missing.values() for gap in gaps)


def timing_coverage(directory, start, end):
    """Check exported timings against independently delivered effort commands.

    Contiguous sequence numbers prove only that the received prefix has no
    holes. They cannot detect queued records that never reached the host.
    Matching command stamps catches that tail without assuming that planner
    searches or detections must continue after the ball has been caught.
    """
    directory = Path(directory)
    raw = directory / 'guest_records.jsonl'
    if not raw.exists() or not raw.stat().st_size:
        return None
    records, missing = _capture(directory)
    return _timing_coverage(directory, start, end, records, missing)


def _timing_coverage(directory, start, end, records, missing):
    streams = {}
    known_control = set()
    for name in STREAMS:
        completions = [r['completed_time'] for r in records[name]
                       if isinstance(r.get('completed_time'), (int, float))
                       and start <= r['completed_time'] <= end]
        last = max(completions) if completions else None
        streams[name] = dict(completed_records=len(completions), last_completion_time=last,
                             tail_seconds=end - last if last is not None else None)
        if name == 'control_timing':
            known_control = {round(r['time'] * 1e9) for r in records[name]
                             if isinstance(r.get('time'), (int, float))
                             and r.get('completed_time', end + 1) <= end}
    commands_path = directory / 'commands.csv'
    sequence_gaps = _missing_count(missing)
    result = dict(sequence_gaps=sequence_gaps, missing_sequences=missing, streams=streams,
                  scope='Sequence gaps cover the entire capture, including preparation. '
                        'Command matching covers the task window; '
                        'neither check proves delivery of every generated record.')
    if not commands_path.exists():
        return dict(result, status='partial' if sequence_gaps else 'unknown')
    with commands_path.open() as source:
        delivered = {round(float(r['command_time']) * 1e9)
                     for r in csv.DictReader(source)
                     if start <= float(r['receipt_time']) <= end}
    missing_control = delivered - known_control
    return dict(result, status='partial' if missing_control or sequence_gaps
                else 'no_missing_delivered_control_timings',
                delivered_command_stamps=len(delivered),
                matched_control_timings=len(delivered) - len(missing_control),
                missing_control_timings=len(missing_control),
                first_missing_command_time=min(missing_control) * 1e-9 if missing_control else None,
                last_missing_command_time=max(missing_control) * 1e-9 if missing_control else None)


def import_guest_records(directory):
    directory = Path(directory)
    records, missing = _capture(directory)
    if not records['control_timing'] or not records['perception']:
        raise ValueError('Guest timing telemetry is missing perception or control records')
    # Validate the entire capture before replacing any per-node files.
    for name, stream in records.items():
        (directory / (name + '.jsonl')).write_text(
            ''.join(json.dumps(record, allow_nan=False) + '\n' for record in stream))
    sequence_gaps = _missing_count(missing)
    result = dict(clock='gem5 simulation', records={name: len(stream) for name, stream in records.items()},
                  status='partial' if sequence_gaps else 'no_sequence_gaps',
                  sequence_gaps=sequence_gaps, missing_sequences=missing,
                  scope='Received completed updates; in-flight records at shutdown are excluded',
                  instrumentation='Per-update clock reads and small reliable ROS records; '
                                  'serialization/export follows the measured computation')
    result_path = directory / 'result.json'
    if result_path.exists():
        task = json.loads(result_path.read_text())
        result['coverage'] = _timing_coverage(
            directory, task['launch_time'], task['result_time'], records, missing)
        if result['coverage']['status'] == 'partial':
            result['status'] = 'partial'
    (directory / 'guest_telemetry.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
