"""Validate guest telemetry and recover the application's existing JSONL streams."""
import json
import math
from pathlib import Path

STREAMS = ('perception', 'interception', 'control', 'control_timing')


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
    (directory / 'guest_telemetry.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
