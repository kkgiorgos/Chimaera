"""Compatibility and integrity for reusable, aggressor-free readiness checkpoints."""
import hashlib
import json
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def key(data, point):
    # The ball is parked outside camera view during readiness. Its launch
    # position, direction, speed and name never enter the guest configuration.
    fixed = {name: value for name, value in data['fixed'].items()
             if name not in ('duration', 'pressure_warmup', 'wall_timeout_s', 'startup_timeout_s')}
    inputs = dict(hardware=data['hardware'], fixed=fixed,
                  rates={name: value for name, value in point['rates'].items() if name != 'name'},
                  ramulator_sha256=data['ramulator_sha256'])
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def groups(data, points):
    result = {}
    for point in points:
        result.setdefault(key(data, point), point)
    return result


def provenance(gem5, kernel, disk, bundle):
    return {name + '_sha256': digest(path) for name, path in
            (('gem5', gem5), ('kernel', kernel), ('disk', disk), ('bundle', bundle))}


def freeze(root, identity, expected_groups):
    root = Path(root)
    records = {}
    for identifier in expected_groups:
        directory = root / identifier
        metadata = json.loads((directory / 'checkpoint.json').read_text())
        if metadata.get('aggressor_enabled') is not False or not (directory / 'cpt/m5.cpt').is_file():
            raise ValueError('Readiness checkpoint is incomplete or contains aggressor traffic')
        required = ('host-replay.bin', 'checkpoint-host-state', 'checkpoint-ready')
        if any(not (directory / name).is_file() for name in required):
            raise ValueError('Readiness checkpoint lacks synchronized host state')
        if metadata.get('transport_backend') == 'm5ops':
            if metadata.get('version') != 2 or not list((directory / 'cpt').glob('*.m5state')):
                raise ValueError('m5ops readiness checkpoint lacks transport state')
            required += ('host-commands.txt', 'host-commands.txt.tail',
                         'guest_records.jsonl', 'gem5/checkpoint-record-counts.json')
            if any(not (directory / name).is_file() for name in required):
                raise ValueError('m5ops readiness checkpoint lacks its physics command prefix')
        if metadata.get('transport_backend') == 'm5ops' and not records_drained(directory):
            raise ValueError('m5ops readiness telemetry prefix has not drained')
        paths = [directory / name for name in (*required, 'checkpoint.json')]
        paths += [path for path in (directory / 'cpt').rglob('*') if path.is_file()]
        paths += sorted((directory / 'gem5').glob('chimaera-trace-*.jsonl'))
        records[identifier] = {str(path.relative_to(directory)): digest(path) for path in paths}
    record = dict(version=1, provenance=identity, groups=records)
    temporary = root / 'index.json.tmp'
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(root / 'index.json')
    return record


def validate(root, data, point, identity):
    root = Path(root)
    index = json.loads((root / 'index.json').read_text())
    if index.get('version') != 1 or index.get('provenance') != identity:
        raise ValueError('Readiness checkpoint execution artifacts differ; prepare a new checkpoint')
    identifier = key(data, point)
    record = index['groups'].get(identifier)
    if not record:
        raise ValueError('No compatible readiness checkpoint for this hardware/camera/rate configuration')
    directory = root / identifier
    for name, expected in record.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not (directory / path).is_file():
            raise ValueError('Invalid or missing readiness checkpoint file: ' + name)
        if digest(directory / path) != expected:
            raise ValueError('Readiness checkpoint changed: ' + name)
    return directory


def records_drained(directory):
    """All paused application's completed records reached the host exactly once."""
    directory = Path(directory)
    counts = directory / 'gem5/checkpoint-record-counts.json'
    if not counts.is_file():
        return False
    expected = json.loads(counts.read_text())
    if (not isinstance(expected, dict) or any(name not in (
            'perception', 'interception', 'control', 'control_timing') or
            type(count) is not int or count < 0 for name, count in expected.items())):
        raise ValueError('Invalid checkpoint record watermarks')
    received = {name: 0 for name in expected}
    path = directory / 'guest_records.jsonl'
    if not path.is_file():
        return False
    with path.open() as stream:
        for line in stream:
            if not line.endswith('\n'):
                return False  # A host callback is still flushing its last line.
            row = json.loads(line)
            name = row['stream']
            if name not in received or row['sequence'] != received[name]:
                raise ValueError('Checkpoint telemetry prefix contains a sequence gap')
            received[name] += 1
            if received[name] > expected[name]:
                raise ValueError('Application produced records after its checkpoint watermark')
    return received == expected
