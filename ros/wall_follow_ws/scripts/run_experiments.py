#!/usr/bin/env python3
"""Run a sequential Cartesian parameter sweep, retaining every attempt."""
import argparse
import itertools
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / 'src/wall_follow_benchmark'))
from wall_follow_benchmark.core import DEFAULTS, validate
from wall_follow_benchmark.world import make_world

LAUNCH_DEFAULTS = dict(DEFAULTS, duration=120., gui=False, lidar_hz=20.,
                       lidar_samples=720, noise_std=0., physics_step=.001, wall_timeout=600., arena_width=12., arena_height=8.)


def make_plan(config, architecture):
    unknown = set(config) - {'fixed', 'sweep', 'repetitions'}
    if unknown:
        raise ValueError(f'Unknown configuration keys: {sorted(unknown)}')
    fixed, sweep = config.get('fixed', {}), config.get('sweep', {})
    repetitions = config.get('repetitions', 3)
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError('repetitions must be a positive integer')
    if not isinstance(fixed, dict) or not isinstance(sweep, dict):
        raise ValueError('fixed and sweep must be objects')
    if set(fixed) & set(sweep):
        raise ValueError('A parameter cannot appear in both fixed and sweep')
    if (set(fixed) | set(sweep)) - set(LAUNCH_DEFAULTS):
        raise ValueError('Unknown launch parameter in fixed or sweep')
    if any(not isinstance(v, list) or not v for v in sweep.values()):
        raise ValueError('Each sweep parameter requires a nonempty list')
    runs = []
    for case, values in enumerate(itertools.product(*sweep.values()), 1):
        params = dict(LAUNCH_DEFAULTS, **fixed)
        params.update(zip(sweep, values))
        for key, default in LAUNCH_DEFAULTS.items():
            value = params[key]
            if type(default) is bool:
                if type(value) is not bool:
                    raise ValueError(f'{key} must be a JSON boolean')
            elif type(default) is int:
                if type(value) is not int:
                    raise ValueError(f'{key} must be a JSON integer')
            else:
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f'{key} must be a finite number')
                params[key] = float(value)
        validate({k: params[k] for k in DEFAULTS})
        make_world(**{k: params[k] for k in ('lidar_hz','lidar_samples','noise_std','physics_step','arena_width','arena_height')})
        if params['duration'] <= 0 or params['wall_timeout'] <= 0:
            raise ValueError('duration and wall_timeout must be positive')
        for rep in range(1, repetitions+1):
            runs.append(dict(id=f'case_{case:03d}_rep_{rep:02d}', parameters=params.copy()))
    return dict(version=1, architecture=architecture, runs=runs)


def completed(path):
    try:
        return (json.loads((path/'metadata.json').read_text()).get('completed') is True
                and (path/'samples.csv').is_file()
                and len((path/'samples.csv').read_text().splitlines()) >= 3)
    except (OSError, ValueError):
        return False


def eligible(path):
    if not completed(path):
        return False
    if not (path/'attempt.json').exists():
        return True  # Manual runs predate the suite runner.
    try:
        return json.loads((path/'attempt.json').read_text()).get('status') == 'completed'
    except (OSError, ValueError):
        return False


def stop_process(process):
    # Each launch owns a new process group, including simulator and bridge children.
    for sig, timeout in ((signal.SIGINT, 10), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=WORKSPACE/'experiments/control_frequency.json')
    parser.add_argument('--output', type=Path, required=True, help='New suite directory (or matching suite with --resume)')
    parser.add_argument('--architecture', required=True)
    parser.add_argument('--resume', action='store_true', help='Skip completed runs; retry failed runs in new attempt directories')
    parser.add_argument('--dry-run', action='store_true', help='Print the validated plan without running or writing anything')
    parser.add_argument('--keep-going', action='store_true', help='Continue after a failed run; still return failure')
    parser.add_argument('--no-plot', action='store_true')
    parser.add_argument('--warmup', type=float, default=5.)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.architecture):
        parser.error('architecture must contain only letters, digits, underscores, dots or hyphens')
    if not math.isfinite(args.warmup) or args.warmup < 0:
        parser.error('warmup must be finite and nonnegative')
    try:
        plan = make_plan(json.loads(args.config.read_text()), args.architecture)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    output = args.output.expanduser().resolve()
    if output.exists():
        if not args.resume:
            parser.error('Output already exists; choose a fresh directory or use --resume')
        try:
            previous = json.loads((output/'suite.json').read_text())
        except (OSError, ValueError):
            parser.error('Cannot resume: missing or invalid suite.json')
        if previous != plan:
            parser.error('Cannot resume: architecture or experiment settings differ from saved plan')
    else:
        output.mkdir(parents=True)
        (output/'suite.json').write_text(json.dumps(plan, indent=2))
    failures = 0
    for index, run in enumerate(plan['runs'], 1):
        parent = output/'runs'/run['id']
        if any(eligible(p) for p in parent.glob('attempt_*')):
            print(f"[{index}/{len(plan['runs'])}] {run['id']}: already completed", flush=True)
            continue
        attempt = 1
        while (parent/f'attempt_{attempt:03d}').exists():
            attempt += 1
        directory = parent/f'attempt_{attempt:03d}'
        directory.mkdir(parents=True)
        params = dict(run['parameters'], architecture=args.architecture, output_dir=str(directory))
        command = ['ros2','launch','wall_follow_benchmark','benchmark.launch.py'] + [
            f'{key}:={str(value).lower() if type(value) is bool else value}' for key,value in params.items()]
        record = dict(command=command, started_unix=time.time(), status='running')
        status_file = directory/'attempt.json'
        status_file.write_text(json.dumps(record, indent=2))
        print(f"[{index}/{len(plan['runs'])}] {run['id']} -> {directory}", flush=True)
        process = None
        interrupted = False
        try:
            with (directory/'launch.log').open('w') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                code = process.wait(timeout=run['parameters']['wall_timeout']+30)
            record.update(returncode=code, status='completed' if code == 0 and completed(directory) else 'failed')
        except subprocess.TimeoutExpired:
            stop_process(process)
            record['status'] = 'timeout'
        except KeyboardInterrupt:
            if process is not None:
                stop_process(process)
            record['status'] = 'interrupted'
            interrupted = True
        except OSError as exc:
            record.update(status='failed', error=str(exc))
        record['finished_unix'] = time.time()
        status_file.write_text(json.dumps(record, indent=2))
        print(f"  {record['status']} (log: {directory/'launch.log'})", flush=True)
        if interrupted:
            return 130
        if record['status'] != 'completed':
            failures += 1
            if not args.keep_going:
                break
    if not args.no_plot:
        code = subprocess.call([sys.executable, str(WORKSPACE/'scripts/compare_experiments.py'),
                                str(output), '--output', str(output/'comparison'), '--warmup', str(args.warmup)])
        if code:
            failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
