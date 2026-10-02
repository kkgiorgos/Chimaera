#!/usr/bin/env python3
"""Run configured jobs in isolated Docker containers using a local daemon."""

import argparse
from collections import deque
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import time
import uuid


def strings(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value) or any(
            not isinstance(item, str) or '\0' in item for item in value):
        raise ValueError(f'{label} must be an array of strings')
    return value


def load(path):
    config = json.loads(path.read_text())
    allowed = {'image', 'command', 'jobs', 'mounts', 'devices', 'env', 'cpus', 'memory', 'workdir'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError(f'configuration keys must be one of {sorted(allowed)}')
    if not isinstance(config.get('image'), str) or not config['image'] or config['image'].startswith('-'):
        raise ValueError('image must be a local Docker image tag or ID')
    strings(config.get('command'), 'command', nonempty=True)
    cpus = config.setdefault('cpus', 2)
    if type(cpus) not in (int, float) or not math.isfinite(cpus) or cpus <= 0:
        raise ValueError('cpus must be positive and finite')
    if not isinstance(config.setdefault('memory', '8g'), str) or not re.fullmatch(
            r'[1-9][0-9]*[bkmgBKMG]?', config['memory']):
        raise ValueError('memory must be a Docker size such as 8g')
    if 'workdir' in config and (not isinstance(config['workdir'], str) or
                               not config['workdir'].startswith('/')):
        raise ValueError('workdir must be an absolute container path')
    jobs = config.get('jobs')
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('jobs must be a nonempty array')
    names = set()
    for job in jobs:
        if not isinstance(job, dict) or set(job) - {'name', 'args', 'env'}:
            raise ValueError('each job accepts name, args, and env')
        name = job.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or name in names:
            raise ValueError('job names must be unique simple names')
        names.add(name)
        strings(job.setdefault('args', []), 'job args')
    for owner in [config, *jobs]:
        env = owner.setdefault('env', {})
        if not isinstance(env, dict) or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                or not isinstance(value, str) or '\0' in value for key, value in env.items()):
            raise ValueError('env must map environment variable names to strings')
    mounts = config.setdefault('mounts', [])
    if not isinstance(mounts, list):
        raise ValueError('mounts must be an array')
    targets = []
    for mount in mounts:
        if not isinstance(mount, dict) or set(mount) != {'source', 'target'}:
            raise ValueError('each read-only mount requires source and target')
        if not isinstance(mount['source'], str) or not isinstance(mount['target'], str):
            raise ValueError('mount paths must be strings')
        source = Path(mount['source']).expanduser()
        mount['source'] = str((path.parent / source).resolve(strict=True))
        target = PurePosixPath(mount['target'])
        if not target.is_absolute() or '..' in target.parts or target == PurePosixPath('/'):
            raise ValueError('mount targets must be absolute paths below /')
        if target == PurePosixPath('/output') or PurePosixPath('/output') in target.parents or target in PurePosixPath('/output').parents:
            raise ValueError('/output is reserved for job results')
        if any(target == other or target in other.parents or other in target.parents for other in targets):
            raise ValueError('mount targets must not overlap')
        targets.append(target)
        mount['target'] = str(target)
        if any(',' in value or '\0' in value for value in mount.values()):
            raise ValueError('mount paths cannot contain commas or NUL')
    strings(config.setdefault('devices', []), 'devices')
    for device in config['devices']:
        if not device.startswith('/'):
            raise ValueError('devices must be absolute host paths')
    return config


def command(config, job, output, image, name):
    argv = ['docker', 'run', '--rm', '--init', '--pull=never', '--name', name,
            '--network', 'none', '--ipc', 'private', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--user', f'{os.getuid()}:{os.getgid()}',
            '--cpus', str(config['cpus']), '--memory', config['memory'],
            '--memory-swap', config['memory'], '--shm-size', '256m',
            '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777',
            '--env', 'HOME=/tmp', '--env', f'CHIMAERA_JOB={job["name"]}']
    groups = {Path(device).stat().st_gid for device in config['devices']}
    groups.update(Path(mount['source']).stat().st_gid for mount in config['mounts'])
    for group in sorted(groups):
        argv += ['--group-add', str(group)]
    for device in config['devices']:
        argv += ['--device', device]
    for key, value in (config['env'] | job['env']).items():
        argv += ['--env', f'{key}={value}']
    if 'workdir' in config:
        argv += ['--workdir', config['workdir']]
    for mount in config['mounts']:
        argv += ['--mount', f'type=bind,source={mount["source"]},target={mount["target"]},readonly']
    argv += ['--mount', f'type=bind,source={output},target=/output', image,
             *config['command'], *job['args']]
    return argv


def save(path, state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    temporary.replace(path)


def tail(path):
    with path.open('rb') as log:
        log.seek(max(0, path.stat().st_size - 2048))
        lines = log.read().decode(errors='replace').splitlines()
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', lines[-1])[-160:] if lines else 'starting'


def run(config, args):
    root = args.output.expanduser().resolve()
    if ',' in str(root):
        raise ValueError('output path cannot contain commas')
    for mount in config['mounts']:
        source = Path(mount['source'])
        if source == root or source in root.parents or root in source.parents:
            raise ValueError('input mounts and output must be separate trees')
    # Resolve once. Every worker uses the same image even if its tag changes.
    image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', config['image']],
                           check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    identity = dict(config=config, image_id=image)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another runner owns this output') from None
        state_path = root / 'run.json'
        if args.resume:
            previous = json.loads(state_path.read_text())
            if previous['identity'] != identity:
                raise ValueError('cannot resume with different configuration or image')
            records = previous['jobs']
            for record in records.values():
                if record.get('container') and record['status'] != 'completed':
                    result = subprocess.run(['docker', 'container', 'inspect', record['container']],
                                            capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        raise ValueError(f'old container still owns results: {record["container"]}; '
                                         'stop/remove it before resuming')
                    if 'No such' not in result.stderr:
                        raise RuntimeError(result.stderr.strip())
        else:
            if any(p.name != '.lock' for p in root.iterdir()):
                raise ValueError('output is not empty; choose a new directory or use --resume')
            records = {job['name']: dict(status='queued') for job in config['jobs']}
        state = dict(identity=identity, jobs=records, status='running')
        pending = deque(job for job in config['jobs'] if records[job['name']]['status'] != 'completed')
        active = {}
        interrupted = None

        def interrupt(signum, _frame):
            nonlocal interrupted
            interrupted = signum

        def remove(name):
            result = subprocess.run(['docker', 'rm', '--force', name], capture_output=True,
                                    text=True, timeout=30)
            if result.returncode and 'No such container' not in result.stderr:
                raise RuntimeError(f'container cleanup failed for {name}: {result.stderr.strip()}')

        handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
        next_progress = 0
        try:
            save(state_path, state)
            while pending or active:
                if interrupted:
                    break
                for name, entry in list(active.items()):
                    process, log, container, started = entry
                    code = process.poll()
                    if code is None:
                        continue
                    # Also handles a disconnected Docker CLI leaving its container alive.
                    remove(container)
                    log.close()
                    records[name].update(status='completed' if code == 0 else 'failed',
                                         exit_code=code, elapsed_seconds=round(time.monotonic() - started, 1))
                    del active[name]
                    print(f'{name}: {records[name]["status"]} (exit {code})', flush=True)
                    save(state_path, state)
                while pending and len(active) < args.workers and not interrupted:
                    job = pending.popleft()
                    name = job['name']
                    output = root / 'jobs' / name
                    output.mkdir(parents=True, exist_ok=True)
                    container = 'chimaera-' + uuid.uuid4().hex
                    argv = command(config, job, output, image, container)
                    records[name] = dict(status='running', container=container, command=argv)
                    log = (output / 'worker.log').open('a')
                    try:
                        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
                    except OSError as exc:
                        log.close()
                        records[name].update(status='failed', exit_code=2, error=str(exc))
                        raise
                    active[name] = (process, log, container, time.monotonic())
                    print(f'Started {name}; log: {output / "worker.log"}', flush=True)
                    save(state_path, state)
                if time.monotonic() >= next_progress:
                    done = sum(record['status'] == 'completed' for record in records.values())
                    failed = sum(record['status'] == 'failed' for record in records.values())
                    print(f'Progress: {done}/{len(records)} completed, {failed} failed, '
                          f'{len(active)} running, {len(pending)} queued', flush=True)
                    for name, (_, _, _, started) in active.items():
                        print(f'  {name} [{int(time.monotonic() - started)}s]: '
                              f'{tail(root / "jobs" / name / "worker.log")}', flush=True)
                    next_progress = time.monotonic() + args.progress_interval
                if active:
                    time.sleep(.2)
            state['status'] = 'interrupted' if interrupted else (
                'completed' if all(r['status'] == 'completed' for r in records.values()) else 'failed')
        except Exception as exc:
            state.update(status='failed', error=str(exc))
            raise
        finally:
            cleanup_errors = []
            for name, (process, log, container, started) in active.items():
                try:
                    # Let the benchmark handle SIGINT before forcing removal.
                    subprocess.run(['docker', 'stop', '--signal', 'SIGINT', '--timeout', '10', container],
                                   capture_output=True, timeout=15)
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    remove(container)
                    records[name].update(status='interrupted', exit_code=process.returncode)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    cleanup_errors.append(str(exc))
                finally:
                    log.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            if cleanup_errors:
                state.update(status='cleanup_failed', errors=cleanup_errors)
            elif active:
                state['status'] = 'interrupted' if interrupted else 'failed'
            save(state_path, state)
            if cleanup_errors:
                raise RuntimeError('; '.join(cleanup_errors))
        print(f'Run {state["status"]}; results: {root}', flush=True)
        return 128 + interrupted if interrupted else int(state['status'] != 'completed')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--workers', type=int, default=1)
    cli.add_argument('--progress-interval', type=float, default=10)
    cli.add_argument('--resume', action='store_true')
    cli.add_argument('--dry-run', action='store_true')
    args = cli.parse_args()
    try:
        if args.workers < 1 or not math.isfinite(args.progress_interval) or args.progress_interval <= 0:
            raise ValueError('workers and progress-interval must be positive')
        config = load(args.config.expanduser().resolve(strict=True))
        if args.dry_run:
            print(json.dumps(config, indent=2))
            return 0
        return run(config, args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
