#!/usr/bin/env python3
"""Run a Cartesian benchmark suite in isolated parallel workers, then compare it."""

import argparse
from collections import deque
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

DIRECTORY = Path(__file__).resolve().parent
SCRIPTS = DIRECTORY.parents[1] / 'ros/wall_follow_ws/scripts'
sys.path.insert(0, str(DIRECTORY))
sys.path.insert(0, str(SCRIPTS))
import worker
from guest_assets import selected, sha256, verify as verify_guest
from run_experiments import eligible, make_plan
from suite_adapter import select_runs


def make_jobs(plan):
    return [dict(name=run['id'], run_ids=[run['id']]) for run in plan['runs']]


def check_assignment(suite, plan, job):
    saved = json.loads((suite / 'suite.json').read_text())
    if (saved['runs'] != select_runs(plan, job['run_ids']) or saved['architecture'] != plan['architecture']
            or saved.get('selected_run_ids') != job['run_ids']):
        raise ValueError('worker plan differs from its assignment')
    unexpected = {p.name for p in (suite / 'runs').glob('*')} - set(job['run_ids'])
    if unexpected:
        raise ValueError(f'unassigned runs {sorted(unexpected)}')


def completed_job(root, plan, job):
    output = root / 'jobs' / job['name']
    if not (output / 'suite/suite.json').exists():
        return False
    check_assignment(output / 'suite', plan, job)
    attempts = [p for p in (output / 'suite/runs' / job['name']).glob('attempt_*') if eligible(p)]
    if len(attempts) > 1:
        raise ValueError(f"multiple completed attempts for {job['name']}")
    if not (output / 'worker.json').is_file():
        return False
    saved = json.loads((output / 'worker.json').read_text())
    return len(attempts) == 1 and saved.get('status') == 'completed' and saved.get('exit_code') == 0


def write_json(path, value):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def publish(root, name, target):
    temporary = root / ('.' + name + '.tmp')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target.relative_to(root), target_is_directory=True)
    temporary.replace(root / name)


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def collect(root, plan, jobs, destination):
    destination.mkdir(parents=True)
    selected, missing, duplicates, errors = {}, [], [], []
    for job in jobs:
        suite = root / 'jobs' / job['name'] / 'suite'
        try:
            check_assignment(suite, plan, job)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{job['name']}: {exc}")
            missing.extend(job['run_ids'])
            continue
        for run_id in job['run_ids']:
            parent = suite / 'runs' / run_id
            gathered = destination / 'runs' / run_id
            if parent.is_dir():
                shutil.copytree(parent, gathered, copy_function=link_or_copy, symlinks=True)
            attempts = sorted(path for path in gathered.glob('attempt_*') if eligible(path))
            if attempts:
                selected[run_id] = str(attempts[0].resolve())
                if len(attempts) > 1:
                    duplicates.append(run_id)
            else:
                missing.append(run_id)
    coverage = dict(planned=[run['id'] for run in plan['runs']], selected=selected,
                    missing=missing, duplicates=duplicates, errors=errors)
    write_json(destination / 'coverage.json', coverage)
    write_json(destination / 'suite.json', plan)
    write_json(destination / 'selected_runs.json', list(selected.values()))
    return coverage


def verify_report(coverage, directory):
    expected = set(coverage['selected'].values())
    included = json.loads((directory / 'included_runs.json').read_text())
    members = json.loads((directory / 'per_run_summary.json').read_text())
    reported = [member['path'] for member in members]
    if (len(included) != len(expected) or set(included) != expected
            or len(reported) != len(expected) or set(reported) != expected):
        raise ValueError('comparison omitted or duplicated collected runs')
    if not (directory / 'dashboard.html').is_file():
        raise ValueError('comparison did not create the dashboard')
    return reported


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, default=SCRIPTS.parent / 'experiments/demo.json')
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--architecture', default='baseline')
    cli.add_argument('--workers', type=int, default=1, help='Maximum concurrent containers')
    cli.add_argument('--image', default='chimaera-worker:jammy-humble-fortress')
    cli.add_argument('--local', action='store_true', help='Run the native controller without guest assets')
    cli.add_argument('--guest-assets', type=Path)
    cli.add_argument('--guest-image', type=Path)
    cli.add_argument('--kernel', type=Path)
    cli.add_argument('--cpus', type=float, default=2, help='CPU limit per container')
    cli.add_argument('--memory', default='8g', help='Memory limit per container')
    rendering = cli.add_mutually_exclusive_group()
    rendering.add_argument('--render-device', type=Path, default=Path('/dev/dri/renderD128'))
    rendering.add_argument('--software-rendering', action='store_true')
    cli.add_argument('--warmup', type=float, default=0)
    cli.add_argument('--interval-us', type=int, default=50000)
    cli.add_argument('--poll-us', type=int, default=10000)
    cli.add_argument('--startup-timeout', type=int, default=300)
    cli.add_argument('--resume', action='store_true')
    cli.add_argument('--dry-run', action='store_true', help='Print assignments without Docker or output writes')
    return cli


def prepare(args):
    if args.workers < 1 or not re.fullmatch(r'[A-Za-z0-9_.-]+', args.architecture):
        raise ValueError('workers must be positive and architecture must be a simple name')
    if (not math.isfinite(args.cpus) or args.cpus <= 0
            or not re.fullmatch(r'[1-9][0-9]*[bkmgBKMG]?', args.memory)
            or not math.isfinite(args.warmup) or args.warmup < 0):
        raise ValueError('invalid CPU, memory, or warmup value')
    if (not 0 < args.poll_us < args.interval_us <= 3600000000
            or args.startup_timeout <= 0):
        raise ValueError('require 0 < poll-us < interval-us, positive startup timeout')
    source = worker.input_file(args.config)
    content = source.read_bytes()
    config = json.loads(content)
    if not isinstance(config, dict):
        raise ValueError('configuration must be a JSON object')
    plan = make_plan(config, args.architecture, gem5=not args.local)
    for run in plan['runs']:
        if run['parameters']['gui']:
            raise ValueError('parallel suites require gui=false')
        if not args.local:
            step = run['parameters']['physics_step'] * 1e9
            if (round(step) < 1 or not math.isclose(step, round(step), abs_tol=1e-6, rel_tol=0)
                    or args.interval_us * 1000 % round(step)):
                raise ValueError('gem5 interval must contain integral nanosecond physics steps')
    jobs = make_jobs(plan)
    for job in jobs:
        select_runs(plan, job['run_ids'])
    return content, plan, jobs


def execution(args, content):
    if os.getuid() == 0:
        raise ValueError('run as a nonroot user with Docker and device access')
    import numpy
    image = json.loads(worker.docker('image', 'inspect', args.image).stdout)[0]
    labels = image['Config'].get('Labels') or {}
    if (image['Os'] != 'linux' or image['Architecture'] != 'amd64'
            or labels.get('io.chimaera.run-selection') != '2'):
        raise ValueError('image lacks run selection; rebuild with build.py --target worker --jobs 5')
    if not args.local and not (args.guest_assets or args.guest_image or args.kernel):
        args.guest_assets = selected(labels.get('io.chimaera.stack', 'jammy-humble-fortress'), image['Id'])
    options = dict(local=args.local, architecture=args.architecture,
                   cpus=args.cpus, memory=args.memory.lower(), warmup=args.warmup,
                   interval_us=args.interval_us, poll_us=args.poll_us,
                   startup_timeout=args.startup_timeout, software_rendering=args.software_rendering)
    assets = {}
    if args.local:
        if args.guest_assets or args.guest_image or args.kernel:
            raise ValueError('--local does not accept guest assets')
    elif args.guest_assets:
        if args.guest_image or args.kernel:
            raise ValueError('--guest-assets cannot be combined with --guest-image or --kernel')
        args.guest_assets = args.guest_assets.expanduser().resolve(strict=True)
        guest = verify_guest(args.guest_assets)
        if guest['profile'] != labels.get('io.chimaera.stack'):
            raise ValueError('guest and worker stack profiles differ')
        assets = dict(manifest_sha256=sha256(args.guest_assets / 'manifest.json'),
                      disk=worker.fingerprint(args.guest_assets / 'disk.img'),
                      kernel=worker.fingerprint(args.guest_assets / 'kernel'))
    else:
        if not args.guest_image or not args.kernel:
            raise ValueError('gem5 requires --guest-assets or both --guest-image and --kernel')
        args.guest_image, args.kernel = worker.input_file(args.guest_image), worker.input_file(args.kernel)
        if args.guest_image == args.kernel:
            raise ValueError('disk and kernel must be separate files')
        assets = dict(disk=worker.fingerprint(args.guest_image), kernel=worker.fingerprint(args.kernel))
    devices = [] if args.local else [Path('/dev/kvm')]
    if not args.software_rendering:
        args.render_device = args.render_device.expanduser().resolve(strict=True)
        devices.append(args.render_device)
        options['render_device'] = str(args.render_device)
        options['render_device_number'] = args.render_device.stat().st_rdev
    for device in devices:
        if not device.is_char_device() or not os.access(device, os.R_OK | os.W_OK):
            raise ValueError(f'inaccessible device: {device}')
    return dict(image_id=image['Id'], config_sha256=hashlib.sha256(content).hexdigest(),
                options=options, assets=assets)


def command(args, root, job, image_id):
    output = root / 'jobs' / job['name']
    argv = [sys.executable, str(DIRECTORY / 'worker.py'), 'run', '--image', image_id,
            '--config', str(root / 'inputs/config.json'), '--output', str(output),
            '--architecture', args.architecture, '--run-ids-file',
            str(root / 'inputs' / (job['name'] + '.json'))]
    for name in ('cpus', 'memory', 'warmup', 'interval_us', 'poll_us', 'startup_timeout'):
        argv += ['--' + name.replace('_', '-'), str(getattr(args, name))]
    if args.local:
        argv.append('--local')
    for name in ('guest_assets', 'guest_image', 'kernel'):
        value = getattr(args, name)
        if value:
            argv += ['--' + name.replace('_', '-'), str(value)]
    argv += ['--software-rendering'] if args.software_rendering else ['--render-device', str(args.render_device)]
    if args.resume and (output / 'worker.json').is_file():
        argv.append('--resume')
    return argv


def run(args, content, plan, jobs):
    root = args.output.expanduser().resolve()
    if ',' in str(root):
        raise ValueError('Docker output paths cannot contain commas')
    identity = execution(args, content)
    identity.update(plan=plan, jobs=jobs)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.orchestration.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another orchestrator owns this output') from None
        state_file = root / 'orchestration.json'
        if state_file.exists():
            if not args.resume:
                raise ValueError('output already exists; choose a new directory or use --resume')
            previous = json.loads(state_file.read_text())
            if previous.get('schema_version') != 2:
                raise ValueError('static suite layout cannot resume with the dynamic scheduler; '
                                 'use the previous runner or choose a fresh output directory')
            if previous['identity'] != identity:
                raise ValueError('cannot resume: inputs, image, assignments, or execution options differ')
            for job in jobs:
                with worker.idle_output(root / 'jobs' / job['name']):
                    pass
        elif args.resume:
            raise ValueError('cannot resume: orchestration.json is missing')
        elif any(path.name != '.orchestration.lock' for path in root.iterdir()):
            raise ValueError('output has no valid orchestration record; choose a new directory')
        skipped = {job['name'] for job in jobs if completed_job(root, plan, job)}
        state = dict(schema_version=2, identity=identity, status='planned')
        if not state_file.exists():
            write_json(state_file, state)
        (root / 'inputs').mkdir(exist_ok=True)
        snapshots = {'config.json': content, **{s['name'] + '.json':
            (json.dumps(s['run_ids'], indent=2) + '\n').encode() for s in jobs}}
        for name, data in snapshots.items():
            path = root / 'inputs' / name
            if path.exists():
                if path.is_symlink() or path.read_bytes() != data:
                    raise ValueError(f'input snapshot differs: {path}')
            else:
                temporary = path.with_name('.' + name + '.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
                path.chmod(0o444)
        token = uuid.uuid4().hex
        launch = root / 'launches' / token
        launch.mkdir(parents=True)
        state.update(status='running', token=token, started_at=datetime.now(timezone.utc).isoformat(),
                     workers_limit=args.workers,
                     jobs=[dict(name=s['name'], run_ids=s['run_ids'],
                                status='skipped' if s['name'] in skipped else 'queued',
                                exit_code=0 if s['name'] in skipped else None) for s in jobs])

        def save():
            write_json(state_file, state)
            write_json(launch / 'orchestration.json', state)

        interrupted = None

        def interrupt(signum, _frame):
            nonlocal interrupted
            interrupted = interrupted or signum

        handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
        processes, logs = [], []

        def drain():
            for process in processes:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
            for process in processes:
                process.wait()

        try:
            save()
            for name in ('comparison', 'gathered'):
                (root / name).unlink(missing_ok=True)
            print(f"Running {len(jobs) - len(skipped)}/{len(jobs)} runs with up to {args.workers} containers; logs: {launch}", flush=True)
            pending = deque((job, record) for job, record in zip(jobs, state['jobs'])
                            if job['name'] not in skipped)
            active = {}
            notified = False
            while pending or active:
                if interrupted and not notified:
                    for process, _, _ in active.values():
                        if process.poll() is None:
                            process.send_signal(interrupted)
                    notified = True
                    print('Stopping workers and waiting for container cleanup...', flush=True)
                for name, (process, record, log) in list(active.items()):
                    code = process.poll()
                    if code is not None:
                        with worker.idle_output(root / 'jobs' / name):
                            pass
                        record.update(exit_code=code, status='completed' if code == 0 else 'failed')
                        log.close()
                        del active[name]
                        print(f"{record['name']} exited {code}", flush=True)
                        save()
                while pending and len(active) < args.workers and not interrupted:
                    job, record = pending.popleft()
                    argv = command(args, root, job, identity['image_id'])
                    record['command'] = argv
                    log = (launch / (job['name'] + '.log')).open('w')
                    logs.append(log)
                    try:
                        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                                   start_new_session=True)
                    except OSError as exc:
                        record.update(status='failed', exit_code=2, error=str(exc))
                        log.close()
                    else:
                        processes.append(process)
                        active[job['name']] = (process, record, log)
                        record['status'] = 'running'
                        print(f"Started {job['name']}", flush=True)
                    save()
                if interrupted and not active:
                    break
                if active:
                    time.sleep(.25)
            if interrupted:
                state.update(status='interrupted', exit_code=128 + interrupted)
                return state['exit_code']
            state['status'] = 'collecting'
            save()
            collection = root / 'collections' / token
            with ExitStack() as idle:
                for job in jobs:
                    idle.enter_context(worker.idle_output(root / 'jobs' / job['name']))
                coverage = collect(root, plan, jobs, collection)
            publish(root, 'gathered', collection)
            state['coverage'] = coverage
            state['collection_sha256'] = sha256(collection / 'coverage.json')
            if interrupted:
                state.update(status='interrupted', exit_code=128 + interrupted)
                return state['exit_code']
            state['status'] = 'comparing'
            save()
            comparison = root / 'reports' / token
            if coverage['selected']:
                comparison.mkdir(parents=True)
                argv = [sys.executable, str(SCRIPTS / 'compare_experiments.py'),
                        '--runs-file', str(collection / 'selected_runs.json'),
                        '--output', str(comparison), '--warmup', str(args.warmup)]
                with (launch / 'comparison.log').open('w') as log:
                    environment = dict(os.environ, MPLCONFIGDIR=str(launch / 'matplotlib'),
                                       PYTHONDONTWRITEBYTECODE='1')
                    process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True, env=environment)
                    processes.append(process)
                    while process.poll() is None:
                        if interrupted:
                            process.send_signal(interrupted)
                            break
                        time.sleep(.25)
                    code = process.wait()
                if interrupted:
                    state.update(status='interrupted', exit_code=128 + interrupted)
                    return state['exit_code']
                if code:
                    raise ValueError(f'comparison failed; see {launch / "comparison.log"}')
                state['reported'] = verify_report(coverage, comparison)
                if interrupted:
                    state.update(status='interrupted', exit_code=128 + interrupted)
                    return state['exit_code']
                partial = (coverage['missing'] or coverage['duplicates'] or coverage['errors']
                           or any(record['exit_code'] != 0 for record in state['jobs']))
                write_json(comparison / 'coverage.json', dict(**coverage,
                    status='partial' if partial else 'completed',
                    collection_sha256=state['collection_sha256']))
                if partial:
                    dashboard = comparison / 'dashboard.html'
                    notice = (f'<aside style="padding:1rem;background:#fff4d9">Partial suite: '
                              f'{len(coverage["selected"])}/{len(plan["runs"])} planned runs. '
                              '<a href="coverage.json">Coverage and diagnostics</a></aside>')
                    dashboard.write_text(dashboard.read_text().replace('<body>', '<body>' + notice, 1))
                if interrupted:
                    state.update(status='interrupted', exit_code=128 + interrupted)
                    return state['exit_code']
                publish(root, 'comparison', comparison)
            if interrupted:
                (root / 'comparison').unlink(missing_ok=True)
                state.update(status='interrupted', exit_code=128 + interrupted)
                return state['exit_code']
            complete = (not coverage['missing'] and not coverage['duplicates'] and not coverage['errors']
                        and len(coverage['selected']) == len(plan['runs'])
                        and all(record['exit_code'] == 0 for record in state['jobs'])
                        and len(state.get('reported', [])) == len(plan['runs']))
            state.update(status='completed' if complete else 'partial', exit_code=0 if complete else 1)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            state.update(status='failed', exit_code=2, error=str(exc))
            raise
        finally:
            try:
                drain()
                if interrupted:
                    (root / 'comparison').unlink(missing_ok=True)
                    state.update(status='interrupted', exit_code=128 + interrupted)
                state['finished_at'] = datetime.now(timezone.utc).isoformat()
                save()
                if interrupted and state['status'] != 'interrupted':
                    (root / 'comparison').unlink(missing_ok=True)
                    state.update(status='interrupted', exit_code=128 + interrupted)
                    save()
            finally:
                for log in logs:
                    log.close()
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        print(f"Suite {state['status']}: {len(state['coverage']['selected'])}/{len(plan['runs'])} runs; {root}", flush=True)
        if state.get('reported') and state['status'] != 'interrupted':
            print(f"Comparison: {root / 'comparison/dashboard.html'}", flush=True)
        return state['exit_code']


def main():
    cli = parser()
    args = cli.parse_args()
    try:
        content, plan, jobs = prepare(args)
        if args.dry_run:
            print(json.dumps(dict(plan=plan, jobs=jobs, requested_workers=args.workers), indent=2))
            return 0
        return run(args, content, plan, jobs)
    except ImportError as exc:
        cli.error(f'host reporting requires NumPy in this Python environment: {exc}')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
