#!/usr/bin/env python3
"""Run a sequential Cartesian parameter sweep, retaining every attempt."""
import argparse
import csv
import fcntl
import itertools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / 'benchmarking'))
from wall_follow_benchmark.configuration import DEFAULTS, validate
from wall_follow_benchmark.hardware import DEFAULTS as HARDWARE_DEFAULTS, validate as validate_hardware
from wall_follow_benchmark.world import make_world
from wall_follow_benchmark.timing import summarize
from wall_follow_benchmark.gem5_stats import summarize as summarize_gem5
from wall_follow_benchmark.sockets import prepare_sockets

LAUNCH_DEFAULTS = dict(DEFAULTS, duration=120., gui=False, lidar_hz=20.,
                       lidar_samples=720, noise_std=0., physics_step=.001, wall_timeout=600., arena_width=12., arena_height=8.)


def make_plan(config, architecture, gem5=False):
    defaults = dict(LAUNCH_DEFAULTS, **(HARDWARE_DEFAULTS if gem5 else {}))
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
    if (set(fixed) | set(sweep)) - set(defaults):
        raise ValueError('Unknown launch parameter in fixed or sweep')
    if any(not isinstance(v, list) or not v for v in sweep.values()):
        raise ValueError('Each sweep parameter requires a nonempty list')
    runs = []
    for case, values in enumerate(itertools.product(*sweep.values()), 1):
        params = dict(defaults, **fixed)
        params.update(zip(sweep, values))
        for key, default in defaults.items():
            value = params[key]
            if type(default) is bool:
                if type(value) is not bool:
                    raise ValueError(f'{key} must be a JSON boolean')
            elif type(default) is int:
                if type(value) is not int:
                    raise ValueError(f'{key} must be a JSON integer')
            elif type(default) is str:
                if type(value) is not str:
                    raise ValueError(f'{key} must be a JSON string')
            else:
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f'{key} must be a finite number')
                params[key] = float(value)
        if gem5:
            validate_hardware(params)
        validate({k: params[k] for k in DEFAULTS})
        make_world(**{k: params[k] for k in ('lidar_hz','lidar_samples','noise_std','physics_step','arena_width','arena_height')})
        if params['duration'] <= 0 or params['wall_timeout'] <= 0:
            raise ValueError('duration and wall_timeout must be positive')
        for rep in range(1, repetitions+1):
            runs.append(dict(id=f'case_{case:03d}_rep_{rep:02d}', parameters=params.copy()))
    return dict(version=1, architecture=architecture, runs=runs)


def completed(path):
    try:
        metadata = json.loads((path/'metadata.json').read_text())
        return (metadata.get('schema_version') in (2, 3) and metadata.get('completed') is True
                and (path/'poses.csv').is_file()
                and (path/'samples.csv').is_file()
                and len((path/'samples.csv').read_text().splitlines()) >= 3)
    except (OSError, ValueError):
        return False


def eligible(path):
    if not completed(path):
        return False
    if not (path/'attempt.json').exists():
        return True  # Direct runtime runs have no suite attempt record.
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader, but also wait for its children.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.1)



class LiveProgress:
    """Render completed co-simulation intervals; never guess unfinished tick progress."""
    def __init__(self, timing_path, stream=None):
        self.timing_path = timing_path
        self.stream = stream if stream is not None else sys.stdout
        self.terminal = self.stream.isatty() and os.environ.get('TERM') != 'dumb'
        self.started = self.updated = time.monotonic()
        self.last_report = 0.
        self.sim_seconds = 0.
        self.step = 0
        self.row = None
        self.timing = None
        self.header = None
        self.pending = ''

    def clear(self):
        if self.terminal:
            self.stream.write('\r\033[2K')
            self.stream.flush()

    def output(self, text):
        if text:
            self.clear()
            self.stream.write(text)
            self.stream.flush()

    def refresh(self):
        if self.timing is None and self.timing_path is not None:
            try:
                self.timing = self.timing_path.open()
            except FileNotFoundError:
                pass
        if self.timing is not None:
            self.pending += self.timing.read()
            while '\n' in self.pending:
                line, self.pending = self.pending.split('\n', 1)
                fields = next(csv.reader([line]))
                if self.header is None:
                    self.header = fields
                    continue
                row = dict(zip(self.header, fields))
                try:
                    step = int(row['step'])
                    simulated = float(row['sim_seconds'])
                    gem5_wall = float(row['gem5_wall_seconds'])
                except (KeyError, ValueError):
                    continue
                if step > self.step:
                    self.step = step
                    self.sim_seconds += simulated
                    self.row = (simulated, gem5_wall)
                    self.updated = time.monotonic()
        now = time.monotonic()
        if not self.terminal and now - self.last_report < 1.:
            return
        self.last_report = now
        if self.timing_path is None:
            text = f' RUNNING | wall {now - self.started:.0f}s'
        elif self.row is None:
            text = f' CHIMAERA | BOOT / FIRST STEP | wall {now - self.started:.0f}s'
        else:
            simulated, gem5_wall = self.row
            rate = simulated / gem5_wall if gem5_wall > 0 else 0.
            text = (f' CHIMAERA | sim {self.sim_seconds:.2f}s | step {self.step}'
                    f' | gem5 {rate:.3f}x | wall {now - self.started:.0f}s'
                    f' | last completed step {now - self.updated:.0f}s ago')
        if self.terminal:
            width = shutil.get_terminal_size(fallback=(120, 24)).columns
            self.stream.write('\r\033[2K' + text[:max(1, width - 1)])
        else:
            self.stream.write(text + '\n')
        self.stream.flush()

    def close(self):
        self.clear()
        if self.timing is not None:
            self.timing.close()


def wait_verbose(process, log_path, timing_path, timeout, stream_logs=True):
    """Show progress and optionally tail logs, retaining normal timeout semantics."""
    display = LiveProgress(timing_path)
    deadline = time.monotonic() + timeout
    pending = ''
    with log_path.open(errors='replace') as reader:
        def drain(final=False):
            nonlocal pending
            if not stream_logs:
                return
            pending += reader.read()
            # Keep partial child lines away from the in-place progress display.
            boundary = len(pending) if final else pending.rfind('\n') + 1
            display.output(pending[:boundary])
            pending = pending[boundary:]
        try:
            while True:
                drain()
                display.refresh()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                try:
                    return process.wait(timeout=min(.2, remaining))
                except subprocess.TimeoutExpired:
                    pass
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop_process(process)
            raise
        finally:
            drain(final=True)
            display.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=WORKSPACE/'experiments/demo.json')
    parser.add_argument('--output', type=Path, required=True, help='New suite directory (or matching suite with --resume)')
    parser.add_argument('--architecture', required=True)
    parser.add_argument('--host-only', action='store_true', help='Launch host.launch.py; start the robot separately with controller.yaml')
    parser.add_argument('--gem5', action='store_true', help='Run each attempt with gem5 and paused Gazebo')
    parser.add_argument('--gem5-root', type=Path, default=WORKSPACE/'../../gem5')
    parser.add_argument('--image', type=Path, help='Previously deployed guest image')
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--interval-us', type=int, default=50000)
    parser.add_argument('--poll-us', type=int, default=10000)
    parser.add_argument('--ratio', type=float, default=1.0, help='Co-simulation pacing target')
    parser.add_argument('--startup-timeout', type=int, default=300)
    parser.add_argument('--resume', action='store_true', help='Skip completed runs; retry failed runs in new attempt directories')
    parser.add_argument('--dry-run', action='store_true', help='Print the validated plan without running or writing anything')
    parser.add_argument('--keep-going', action='store_true', help='Continue after a failed run; still return failure')
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument('--progress', action='store_true', help='Show live simulation status only; keep launch output in launch.log')
    verbosity.add_argument('--verbose', action='store_true', help='Stream launch output and show live simulation progress; retain launch.log')
    parser.add_argument('--no-plot', action='store_true')
    parser.add_argument('--warmup', type=float, default=5.)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.architecture):
        parser.error('architecture must contain only letters, digits, underscores, dots or hyphens')
    if not math.isfinite(args.warmup) or args.warmup < 0:
        parser.error('warmup must be finite and nonnegative')
    try:
        plan = make_plan(json.loads(args.config.read_text()), args.architecture, gem5=args.gem5)
        plan['deployment'] = 'gem5' if args.gem5 else 'host_only' if args.host_only else 'local'
        if args.gem5:
            if args.host_only:
                raise ValueError('--gem5 and --host-only are mutually exclusive')
            if not 0 < args.poll_us < args.interval_us <= 3600000000:
                raise ValueError('require 0 < poll-us < interval-us <= 3600000000')
            if not math.isfinite(args.ratio) or args.ratio <= 0 or args.startup_timeout <= 0:
                raise ValueError('ratio and startup-timeout must be positive and finite')
            root = args.gem5_root.expanduser().resolve()
            image = (args.image or root/'resources/x86-ubuntu-22.04-ros-humble.img').expanduser().resolve()
            kernel = (args.kernel or root/'resources/x86-linux-kernel-5.15.180').expanduser().resolve()
            plan['gem5'] = dict(gem5_root=str(root), image=str(image), kernel=str(kernel),
                                interval_us=args.interval_us, poll_us=args.poll_us,
                                ratio=args.ratio, startup_timeout_s=args.startup_timeout)
            for run in plan['runs']:
                ns = run['parameters']['physics_step'] * 1e9
                if round(ns) < 1 or not math.isclose(ns, round(ns), rel_tol=0, abs_tol=1e-6) or args.interval_us * 1000 % round(ns):
                    raise ValueError('gem5 interval must be an integral number of nanosecond physics steps')
            if not args.dry_run:
                for path in (root/'build/X86/gem5.opt', image, kernel):
                    if not path.is_file():
                        raise ValueError(f'Missing gem5 resource: {path}')
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    # Fixed Chimaera data sockets allow only one suite at a time.
    suite_lock = None
    if args.gem5:
        suite_lock = open('/tmp/chimaera-wall-follow-suite.lock', 'a')
        try:
            fcntl.flock(suite_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another gem5 suite is running (shared Chimaera sockets)')
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
        params = run['parameters']
        sensor = {k: params[k] for k in ('lidar_hz', 'lidar_samples', 'noise_std', 'physics_step')}
        arena = {k: params[k] for k in ('arena_width', 'arena_height')}
        (directory/'world.sdf').write_text(make_world(**sensor, **arena))
        (directory/'experiment.json').write_text(json.dumps(dict(sensor=sensor, arena=arena,
            architecture=args.architecture, gui=params['gui'],
            hardware={k: params[k] for k in HARDWARE_DEFAULTS} if args.gem5 else None,
            source_sha256={str(p.relative_to(WORKSPACE/'src/wall_follow_robot')):
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((WORKSPACE/'src/wall_follow_robot').rglob('*')) if p.suffix in ('.hpp', '.cpp')}), indent=2))
        controller = {k: params[k] for k in DEFAULTS}
        (directory/'controller.yaml').write_text(json.dumps({'wall_follower': {'ros__parameters': controller}}, indent=2))
        launch_args = dict(world=str(directory/'world.sdf'), parameters_file=str(directory/'controller.yaml'),
                           output_dir=str(directory), duration=params['duration'], gui=params['gui'], wall_timeout=params['wall_timeout'])
        if args.gem5:
            launch_args.update({k: params[k] for k in HARDWARE_DEFAULTS})
            launch_args.update(plan['gem5'], outdir=str(directory/'gem5'),
                               timing_socket=str(Path('/tmp/chimaera_time.sock')),
                               physics_step_ns=round(params['physics_step'] * 1e9), status_bar=False)
        command = ['ros2','launch','wall_follow_bridge' if args.gem5 else 'wall_follow_benchmark',
                   'bringup.launch.py' if args.gem5 else 'host.launch.py' if args.host_only else 'benchmark.launch.py'] + [
            f'{key}:={str(value).lower() if type(value) is bool else value}' for key,value in launch_args.items()]
        record = dict(command=command, started_unix=time.time(), status='running')
        status_file = directory/'attempt.json'
        status_file.write_text(json.dumps(record, indent=2))
        print(f"[{index}/{len(plan['runs'])}] {run['id']} -> {directory}", flush=True)
        process = None
        interrupted = False
        try:
            with (directory/'launch.log').open('w') as log:
                if args.gem5:
                    for path in prepare_sockets():
                        log.write(f'Removed inactive Chimaera socket: {path}\n')
                    log.flush()
                environment = dict(os.environ, PYTHONUNBUFFERED='1') if args.verbose else None
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env=environment)
                timeout = run['parameters']['wall_timeout'] + 30
                code = (wait_verbose(process, directory/'launch.log',
                                     directory/'timing.csv' if args.gem5 else None, timeout,
                                     stream_logs=args.verbose)
                        if args.progress or args.verbose else process.wait(timeout=timeout))
            record.update(returncode=code, status='completed' if code == 0 and completed(directory) else 'failed')
        except subprocess.TimeoutExpired:
            stop_process(process)
            record['status'] = 'timeout'
        except KeyboardInterrupt:
            if process is not None:
                stop_process(process)
            record['status'] = 'interrupted'
            interrupted = True
        except (OSError, RuntimeError) as exc:
            record.update(status='failed', error=str(exc))
            print(f'  Launch/preflight failed: {exc}', flush=True)
        if args.gem5:
            try:
                timing = summarize(directory/'timing.csv')
                (directory/'timing_summary.json').write_text(json.dumps(timing, indent=2))
                record['timing'] = timing
                print(f"  Co-simulation: {timing['cosim_realtime_factor']:.3f} simulated s/wall s", flush=True)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record['timing_error'] = str(exc)
                if record['status'] == 'completed':
                    record['status'] = 'failed'
        if args.gem5:
            try:
                stats = summarize_gem5(directory/'gem5/stats.txt')
                (directory/'gem5_summary.json').write_text(json.dumps(stats, indent=2))
                record['gem5_stats'] = stats
            except (OSError, ValueError) as exc:
                record['gem5_stats_error'] = str(exc)
                if record['status'] == 'completed':
                    record['status'] = 'failed'
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
