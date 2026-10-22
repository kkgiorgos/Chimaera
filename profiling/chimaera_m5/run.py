#!/usr/bin/env python3
"""Run a frozen matrix serially only after the parent's actual KVM catching pass."""
import argparse
import datetime
import fcntl
import os
from pathlib import Path
import time
import traceback

from common import read_json, sha256, tree_hashes, validate_config, write_json


def check_file(record):
    path = Path(record['path'])
    if path.stat().st_size != record['bytes'] or sha256(path) != record['sha256']:
        raise ValueError('Frozen artifact changed: ' + str(path))


def check_gate(path, physical_host):
    gate = read_json(path)
    expected = dict(experiment='actual_ball_catching', cpu_type='kvm', status='pass',
                    physical_host=physical_host)
    if not gate or any(gate.get(key) != value for key, value in expected.items()):
        raise ValueError('Require parent-attested actual KVM ball-catching pass on this host')
    if not gate.get('announced_by_parent') or not gate.get('evidence'):
        raise ValueError('Catch gate needs parent announcement and retained evidence')
    for record in gate['evidence']:
        check_file(record)
    return gate


def check_variant(entry):
    if not entry.get('ready'):
        raise ValueError('Selection not ready: ' + '; '.join(entry.get('problems', [])))
    if tree_hashes(entry['runtime']) != entry['runtime_files']:
        raise ValueError('Frozen runtime tree changed')
    for key in ('source', 'source_snapshot', 'bundle', 'module', 'retained_bundle', 'stage_provenance'):
        if key in entry:
            check_file(entry[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--gate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', help='Explicit serial subset; remaining cases stay not_run')
    parser.add_argument('--append', action='store_true')
    args = parser.parse_args()
    lock, matrix = read_json(args.lock), read_json(args.matrix)
    gate = check_gate(args.gate, lock['physical_host'])  # Before runtime imports/preflight.
    if lock['schema_version'] != 1 or matrix['schema_version'] != 1:
        parser.error('Unsupported schema')
    names = [case['name'] for case in matrix['cases']]
    if len(set(names)) != len(names) or any(Path(name).name != name for name in names):
        parser.error('Case names must be unique single path components')
    if args.cases and set(args.cases) - set(names):
        parser.error('Unknown case selection')
    fingerprint = dict(matrix=sha256(args.matrix), lock=sha256(args.lock), gate=sha256(args.gate))
    args.output.mkdir(parents=True, exist_ok=args.append)
    identity = args.output / 'suite.json'
    if args.append:
        previous = read_json(identity)
        if previous is None or previous['input_sha256'] != fingerprint:
            parser.error('Append requires the identical frozen suite, lock and gate')
    else:
        write_json(identity, dict(schema_version=1, input_sha256=fingerprint,
            matrix=matrix, lock=lock, gate=gate))
    # This lock coordinates this runner. The parent must also serialize other tools
    # using Chimaera's fixed socket paths; it is not a detector for unrelated runs.
    with Path('/tmp/chimaera-m5-benchmark.lock').open('a') as serial:
        fcntl.flock(serial, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for value in lock['assets'].values():
            check_file(value)
        if tree_hashes(Path(__file__).resolve().parent) != lock['tooling_files']:
            raise ValueError('Frozen benchmark tooling changed')
        from chimaera_ros import latency
        orchestration = lock['orchestration']
        if Path(latency.__file__).resolve().parent != Path(orchestration['path']):
            raise ValueError('Imported orchestration package differs from frozen selection')
        if tree_hashes(orchestration['path']) != orchestration['files']:
            raise ValueError('Frozen orchestration package changed')
        from adapter import install
        active = install(latency)
        for case in matrix['cases']:
            if args.cases and case['name'] not in args.cases:
                continue
            directory = args.output / case['name']
            if directory.exists():
                if args.append:
                    continue  # Never overwrite or silently retry a failed attempt.
                raise ValueError('Existing attempt: ' + str(directory))
            entry = lock['variants'][case['implementation']]
            config = dict(case['config'], transport_backend=entry.get('backend',
                entry.get('selection', {}).get('backend')),
                middleware_transport=entry.get('middleware', entry.get('selection', {}).get('middleware')))
            if entry.get('module'):
                module_key = 'm5_module' if config['transport_backend'] == 'm5ops' else 'dma_module'
                config[module_key] = entry['module']['path']
                if module_key == 'dma_module':
                    config['dma_module_options'] = entry['selection'].get('module_options', '')
            record = dict(schema_version=1, case=case, physical_host=lock['physical_host'],
                input_sha256=fingerprint, started=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                status='running', fresh_boot=True, cpu_max=read_limit('cpu.max'),
                memory_max=read_limit('memory.max'), loadavg=list(os.getloadavg()),
                implementation=case['implementation'], environment=lock['environment'])
            write_json(args.output / 'active-attempt.json', record)
            start = time.monotonic()
            interrupted = False
            try:
                validate_config(config)
                check_variant(entry)
                active.clear()
                active.update(config)
                active.update(implementation=case['implementation'], selection=entry,
                              sdk_image=lock['environment']['sdk_image'])
                runtime = Path(entry['runtime'])
                env = dict(os.environ, CHIMAERA_PROFILE='0')
                for key in list(env):
                    if key.startswith('CHIMAERA_DDS_') or key == 'CHIMAERA_M5_DEVICE':
                        env.pop(key)
                for key, prefix in (('LD_LIBRARY_PATH', runtime / 'lib'),
                                    ('AMENT_PREFIX_PATH', runtime)):
                    env[key] = str(prefix) + os.pathsep + env.get(key, '')
                record['launch_config'] = config
                record['environment_paths'] = {key: env[key] for key in ('LD_LIBRARY_PATH', 'AMENT_PREFIX_PATH')}
                execution_started = time.monotonic()
                try:
                    latency.run_case(directory, config, runtime, Path(lock['gem5_root']),
                        Path(lock['assets']['image']['path']), Path(lock['assets']['kernel']['path']),
                        bundle=Path(entry['bundle']['path']), environment=env)
                finally:
                    record['execution_wall_seconds'] = time.monotonic() - execution_started
                    if active.get('legacy_validation'):
                        record['legacy_validation'] = active['legacy_validation']
                check_variant(entry)
                record['status'] = 'complete'
            except KeyboardInterrupt:
                record.update(status='cancelled', error='Interrupted by operator')
                interrupted = True
            except Exception as error:
                record.update(status='failed', error=str(error), traceback=traceback.format_exc())
            record['wall_seconds'] = time.monotonic() - start
            directory.mkdir(exist_ok=True)
            write_json(directory / 'benchmark-attempt.json', record)
            write_json(args.output / 'progress.json', {
                name: read_json(args.output / name / 'benchmark-attempt.json', {}).get('status', 'not_run')
                for name in names})
            print(case['name'], record['status'], flush=True)
            if interrupted:
                break
        # Rehash immutable large assets once per serial batch, not once per case.
        for value in lock['assets'].values():
            check_file(value)
    return 0 if all(read_json(args.output / name / 'benchmark-attempt.json', {}).get('status')
                    == 'complete' for name in names) else 1


def read_limit(name):
    path = Path('/sys/fs/cgroup') / name
    return path.read_text().strip() if path.is_file() else None


if __name__ == '__main__':
    raise SystemExit(main())
