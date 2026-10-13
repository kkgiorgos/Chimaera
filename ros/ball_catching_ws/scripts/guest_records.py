"""Validate guest telemetry and recover the application's existing JSONL streams."""
import json
import math
import csv
from pathlib import Path

STREAMS = ('perception', 'interception', 'control', 'control_timing')


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
    streams = {}
    known_control = set()
    for name in STREAMS:
        path = directory / (name + '.jsonl')
        records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        completions = [r['completed_time'] for r in records
                       if isinstance(r.get('completed_time'), (int, float))
                       and start <= r['completed_time'] <= end]
        last = max(completions) if completions else None
        streams[name] = dict(completed_records=len(completions), last_completion_time=last,
                             tail_seconds=end - last if last is not None else None)
        if name == 'control_timing':
            known_control = {round(r['time'] * 1e9) for r in records
                             if isinstance(r.get('time'), (int, float))
                             and r.get('completed_time', end + 1) <= end}
    commands_path = directory / 'commands.csv'
    if not commands_path.exists():
        return dict(status='unknown', streams=streams)
    with commands_path.open() as source:
        delivered = {round(float(r['command_time']) * 1e9)
                     for r in csv.DictReader(source)
                     if start <= float(r['receipt_time']) <= end}
    missing = delivered - known_control
    return dict(status='partial' if missing else 'no_missing_delivered_control_timings',
                delivered_command_stamps=len(delivered),
                matched_control_timings=len(delivered) - len(missing),
                missing_control_timings=len(missing),
                first_missing_command_time=min(missing) * 1e-9 if missing else None,
                last_missing_command_time=max(missing) * 1e-9 if missing else None,
                streams=streams,
                scope='Matches independently received effort commands; '
                      'does not prove delivery of every generated record')


def import_guest_records(directory):
    directory = Path(directory)
    source = directory / 'guest_records.jsonl'
    records = {name: [] for name in STREAMS}
    if not source.exists():
        raise ValueError('Guest timing telemetry was not recorded')
    for line in source.read_text().splitlines():
        envelope = json.loads(line)
        name = envelope['stream']
        if name not in records:
            raise ValueError('Unknown guest telemetry stream: ' + str(name))
        sequence = envelope['sequence']
        if type(sequence) is not int or sequence != len(records[name]):
            raise ValueError(f'Guest telemetry sequence gap in {name}: '
                             f'expected {len(records[name])}, received {sequence}')
        record = envelope['record']
        completed = record.get('completed_time')
        if (record.get('timing_clock') != 'gem5 simulation'
                or type(completed) not in (int, float) or not math.isfinite(completed)):
            raise ValueError('Guest telemetry needs an authoritative gem5 completion timestamp')
        # Also reject nonstandard JSON NaN/Infinity anywhere in a record.
        records[name].append(json.dumps(record, allow_nan=False))
    if not records['control_timing'] or not records['perception']:
        raise ValueError('Guest timing telemetry is missing perception or control records')
    # Validate the entire capture before replacing any per-node files.
    for name, lines in records.items():
        (directory / (name + '.jsonl')).write_text(''.join(line + '\n' for line in lines))
    result = dict(clock='gem5 simulation', records={name: len(lines) for name, lines in records.items()},
                  sequence_gaps=0,
                  scope='Received completed updates; in-flight records at shutdown are excluded',
                  instrumentation='Per-update clock reads and small reliable ROS records; '
                                  'serialization/export follows the measured computation')
    result_path = directory / 'result.json'
    if result_path.exists():
        task = json.loads(result_path.read_text())
        result['coverage'] = timing_coverage(directory, task['launch_time'], task['result_time'])
    (directory / 'guest_telemetry.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
