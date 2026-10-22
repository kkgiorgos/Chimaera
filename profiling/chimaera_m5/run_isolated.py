#!/usr/bin/env python3
"""Run frozen cases serially in fresh Docker containers with private /tmp.

The frozen run.py owns simulation and attempt records. This host wrapper owns
only its uniquely labelled containers and a separate operator log per invocation.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

from common import read_json, sha256, tree_hashes, write_json


OWNER_LABEL = 'chimaera.m5.operator'


def retained_status(record):
    try:
        attempt = read_json(record)
        return (attempt.get('status', 'invalid'), attempt.get('error')) if isinstance(attempt, dict) else (
            'invalid', 'Attempt record must be an object')
    except (OSError, ValueError) as error:
        return 'invalid', str(error)


def stop_owned(container, token, output):
    """Never stop a container unless its ownership label matches this invocation."""
    inspected = subprocess.run(['docker', 'inspect', '--format',
        '{{ index .Config.Labels "' + OWNER_LABEL + '" }}', container],
        capture_output=True, text=True, check=False, timeout=15)
    if inspected.returncode == 0 and inspected.stdout.strip() == token:
        subprocess.run(['docker', 'stop', '--timeout', '5', container],
            stdout=output, stderr=subprocess.STDOUT, check=False, timeout=20)


def prior_operator_failure(logroot, case):
    for path in sorted(logroot.rglob(case + '.json')):
        try:
            note = read_json(path, {})
        except (OSError, ValueError):
            return 'invalid_operator_record'
        if note.get('success') is False or note.get('status') in ('timeout', 'cancelled', 'launch_failed'):
            return note.get('status', 'failed')
    return None


def run_cases(args):
    root = args.root.resolve()
    tooling = Path(__file__).resolve().parent
    lock = read_json(args.lock)
    matrix = read_json(args.matrix)
    sdk = lock['environment']['sdk_image']
    if not isinstance(sdk, str) or not sdk.startswith('sha256:') or len(sdk) != 71:
        raise ValueError('Lock must pin an immutable SDK image SHA-256')
    if tree_hashes(tooling) != lock['tooling_files']:
        raise ValueError('Frozen benchmark tooling changed')
    suite = read_json(args.output / 'suite.json')
    if suite is not None and suite.get('input_sha256') != {
            key: sha256(path) for key, path in (('matrix', args.matrix), ('lock', args.lock), ('gate', args.gate))}:
        raise ValueError('Resume requires the identical frozen matrix, lock and gate')
    names = [case['name'] for case in matrix['cases']]
    if len(set(names)) != len(names) or any(Path(name).name != name or name in ('.', '..') for name in names):
        raise ValueError('Case names must be unique single path components')
    if args.cases and (len(set(args.cases)) != len(args.cases) or set(args.cases) - set(names)):
        raise ValueError('Unknown or duplicate case selection')
    cases = list(args.cases) if args.cases else names
    if not cases:
        raise ValueError('No cases selected')

    def mounted(path):
        # CLI paths refer to the host checkout; the lock records paths under /work.
        return str(Path('/work') / Path(path).resolve().relative_to(root))

    matrix_path, suite_path, lock_path, gate_path, tooling_path = map(mounted,
        (args.matrix, args.output, args.lock, args.gate, tooling))
    setup_paths = [mounted(args.middleware_setup), mounted(args.runtime_setup)]
    token = uuid.uuid4().hex
    operator_root = args.output.parent / (args.output.name + '-operator')
    logroot = operator_root / token
    logroot.mkdir(parents=True, exist_ok=False)
    notes, cancelled = [], False
    for index, name in enumerate(cases):
        record = args.output / name / 'benchmark-attempt.json'
        if record.exists():
            status, error = retained_status(record)
            operator_failure = prior_operator_failure(operator_root, name)
            notes.append(dict(case=name, status=operator_failure or status, attempt_status=status,
                error=error, retained=True, success=status == 'complete' and operator_failure is None))
            print(name, 'retained', status, flush=True)
            continue
        if (args.output / name).exists() or prior_operator_failure(operator_root, name):
            notes.append(dict(case=name, status='incomplete', retained=True, success=False,
                error='Existing attempt directory has no result; retained without retry'))
            continue
        container = 'chimaera-m5-' + token + '-' + str(index)
        marker = logroot / (name + '-runner.json')
        command = ['docker', 'run', '--rm', '-i', '--name', container,
            '--label', OWNER_LABEL + '=' + token, '--cpus', '4', '--memory', '24g',
            '--device', '/dev/kvm', '--shm-size', '2g', '--network', 'none',
            '--entrypoint', 'bash', '--mount', 'type=bind,src=' + str(root) + ',dst=/work',
            '--workdir', '/work', sdk, tooling_path + '/container_case.sh',
            matrix_path, suite_path, name, '--lock', lock_path, '--gate', gate_path,
            '--middleware-setup', setup_paths[0], '--runtime-setup', setup_paths[1],
            '--completion-record', mounted(marker), '--operator-token', token]
        print('START', name, flush=True)
        started, operator_status, code, cleanup_error = time.monotonic(), None, None, None
        with (logroot / (name + '.log')).open('x') as output:
            try:
                code = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT,
                                      timeout=args.operator_timeout, check=False).returncode
            except subprocess.TimeoutExpired:
                operator_status, code = 'timeout', 124
            except KeyboardInterrupt:
                operator_status, code, cancelled = 'cancelled', 130, True
            except OSError as error:
                operator_status = 'launch_failed'
                cleanup_error = str(error)
            finally:
                if operator_status in ('timeout', 'cancelled'):
                    try:
                        stop_owned(container, token, output)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        cleanup_error = str(error)
        status, error = retained_status(record) if record.exists() else ('missing_attempt', None)
        try:
            completion = read_json(marker, {})
        except (OSError, ValueError):
            completion = {}
        # run.py returns 1 when other planned cases remain unfinished. Require
        # proof of its normal return so an uncaught integrity error cannot pass.
        normal_return = completion == dict(operator_token=token, case=name, runner_return_code=code)
        success = status == 'complete' and operator_status is None and code in (0, 1) and normal_return
        note = dict(case=name, container=container, docker_exit_code=code,
            operator_wall_timeout_s=args.operator_timeout, wall_seconds=time.monotonic() - started,
            status=operator_status or (status if success or status != 'complete' else 'runner_failed'),
            attempt_status=status, error=error,
            cleanup_error=cleanup_error, normal_runner_return=normal_return, success=success,
            command=command)
        notes.append(note)
        write_json(logroot / (name + '.json'), note)
        print('FINISH', name, note['status'], error or '', flush=True)
        if cancelled:
            break
    result = dict(selected_cases=cases, attempts=notes, failures=[note for note in notes if not note['success']],
                  unstarted_cases=cases[len(notes):], cancelled=cancelled, sdk_image=sdk)
    write_json(logroot / 'result.json', result)
    return 130 if cancelled else int(bool(result['failures']))


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--gate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--root', type=Path, default=root)
    parser.add_argument('--middleware-setup', type=Path,
                        default=root / '.chimaera-middleware/runtime-staging-r2/local_setup.bash')
    parser.add_argument('--runtime-setup', type=Path, default=root / '.chimaera-m5/runtime/local_setup.bash')
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--operator-timeout', type=float, default=15000)
    args = parser.parse_args()
    if args.operator_timeout <= 0:
        parser.error('Positive operator timeout required')
    return run_cases(args)


if __name__ == '__main__':
    raise SystemExit(main())
