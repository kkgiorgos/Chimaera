"""Local/LAN benchmark workbench. No Python or JavaScript web framework required."""
import argparse
import copy
import csv
import fcntl
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import shlex
import shutil
import signal
import struct
import subprocess
import tarfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .latency import MODES, UNSET, driver_failure, make_report, measurements, read_events, run_case, stage_guest, write_json
from .latency_session import SweepSessions, ordered_cases
from .latency_startup import startup_status
from .latency_config import DEFAULT, HARDWARE, plan_sweep
from .latency_budget import wall_budget

ACTIVE = ('running', 'preparing', 'stopping')
STREAMS = {'driver': 'driver.log', 'host': 'host.log', 'guest': 'guest.log',
           'gazebo': 'gazebo.log', 'gem5': 'gem5.log',
           'serial': 'gem5/board.pc.com_1.device', 'stats': 'gem5/stats.txt'}


def read_json(path, fallback=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return fallback


def tail(path, limit=65536):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size-limit))
            return dict(text=stream.read().decode('utf-8', errors='replace'), truncated=size > limit)
    except FileNotFoundError:
        return dict(text='No output has been written to this log yet.', truncated=False)


def clock_value(path):
    try:
        value = path.read_bytes()
        magic, timestamp = struct.unpack('<QQ', value)
        return timestamp if magic == 0x434c42434c4f434b and timestamp != UNSET else None
    except (FileNotFoundError, struct.error):
        return None


def samples(directory, direction, stream=None):
    config = read_json(directory / 'run.json')
    if not config or config['status'] != 'complete':
        raise ValueError('Samples are available after a case completes')
    if direction not in ('host_to_guest', 'guest_to_host'):
        raise ValueError('Unknown message direction')
    destination = 'guest' if direction == 'host_to_guest' else 'host'
    start = round(config['warmup'] * 1e9)
    stop = start + round(config['duration'] * 1e9)
    seen, trace, values = set(), [], []
    for row in read_events(directory):
        if row['side'] != destination or row['event'] != 'receive' or (stream and row['stream']!=stream):
            continue
        published, received = row['publish_ns'], row['callback_ns']
        identity = row['stream'], row['sequence']
        if not start <= published < stop or identity in seen:
            continue
        seen.add(identity)
        latency = (received-published) / 1e6
        values.append(latency)
        trace.append([(published-start) / 1e9, latency])
    values.sort()
    trace.sort()
    count = len(values)
    indices = sorted({round(i*(count-1)/min(1999, count-1)) for i in range(min(2000, count))}) if count > 1 else list(range(count))
    cdf = [[values[i], (i+1)/count] for i in indices]
    # Retain local extremes in the time plot, rather than hiding spikes with a
    # simple every-nth-point sample. Summaries always use every received message.
    if len(trace) > 2000:
        stride = (len(trace)+999)//1000
        reduced = []
        for pos in range(0, len(trace), stride):
            bucket = trace[pos:pos+stride]
            reduced.extend(sorted([min(bucket, key=lambda p: p[1]), max(bucket, key=lambda p: p[1])]))
        trace = reduced
    return dict(count=count, cdf=cdf, trace=trace, plot_point_limit=2000,
                note='Plots retain at most 2,000 points; summary statistics use all received messages.')


class Workbench:
    def __init__(self, repo, data, runtime_prefix=None, executor=run_case):
        self.repo, self.data = Path(repo).resolve(), Path(data).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / 'sweeps').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.worker = None
        self.executor = executor
        self.jobs = {}
        self.reports = {}
        self.bundle_checks = {}
        for path in (self.data / 'sweeps').glob('*/sweep.json'):
            job = read_json(path)
            if job['status'] in ACTIVE:
                job.update(status='interrupted', error='The workbench stopped before this job completed.', finished=time.time())
                write_json(path, job)
            self.jobs[job['id']] = job
        default_prefix = self.repo / 'ros/chimaera_ros_ws/install/chimaera_ros_bridge'
        self.runtime = read_json(self.data / 'runtime.json', dict(
            prefix=str(default_prefix), gem5_root=str(self.repo / 'gem5'),
            image=str(self.repo / 'gem5/resources/x86-ubuntu-22.04-ros-humble.img'),
            kernel=str(self.repo / 'gem5/resources/x86-linux-kernel-5.15.180'), bundle=''))
        if runtime_prefix:
            self.runtime['prefix'] = str(Path(runtime_prefix).resolve())

    def _save(self, job):
        write_json(self.data / 'sweeps' / job['id'] / 'sweep.json', job)

    def job(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise ValueError('Sweep not found')
            return copy.deepcopy(self.jobs[identifier])

    def busy(self):
        return any(job['status'] in ACTIVE for job in self.jobs.values())

    def set_runtime(self, value):
        with self.lock:
            if self.busy():
                raise ValueError('Wait for the active job to finish before changing runtime paths')
            if not isinstance(value, dict) or set(value) != set(self.runtime):
                raise ValueError('Expected prefix, gem5_root, image, kernel and bundle paths')
            for key, path in value.items():
                if not isinstance(path, str) or '\0' in path or len(path) > 4096 or (not path and key != 'bundle'):
                    raise ValueError('Invalid runtime path')
            self.runtime = {key: str(Path(path).expanduser().resolve()) if path else '' for key, path in value.items()}
            write_json(self.data / 'runtime.json', self.runtime)
        return self.preflight()

    def preflight(self):
        runtime = dict(self.runtime)
        prefix, gem5 = Path(runtime['prefix']), Path(runtime['gem5_root'])
        executable = prefix / 'lib/chimaera_ros_bridge'
        checks = [dict(key='ros', label='ROS 2 Humble', ready=Path('/opt/ros/humble/setup.bash').is_file()),
            dict(key='gazebo', label='Gazebo Fortress', ready=shutil.which('ign') is not None),
            dict(key='runtime', label='Benchmark runtime', ready=all(os.access(executable/name, os.X_OK)
                for name in ('latency_endpoint','latency_driver','guest_bridge')) and all((prefix/path).is_file()
                for path in ('lib/libchimaera_latency_clock.so', 'lib/libchimaera_gem5_clock.so',
                    'lib/libchimaera_gem5_clock_addr.so', 'share/chimaera_ros_bridge/local_setup.bash',
                    'share/chimaera_ros_bridge/config/latency_capabilities.json'))),
            dict(key='kvm', label='KVM boot', ready=os.access('/dev/kvm', os.R_OK | os.W_OK)),
            dict(key='simulator', label='Gem5 binary', ready=(gem5/'build/X86/gem5.opt').is_file()),
            dict(key='image', label='Guest disk and kernel', ready=Path(runtime['image']).is_file() and Path(runtime['kernel']).is_file()),
            dict(key='bundle', label='Staged guest bundle (v4)', ready=self._bundle_ready(runtime['bundle'])),
            dict(key='compiler', label='Colcon and CMake', ready=shutil.which('colcon') is not None and shutil.which('cmake') is not None)]
        supported = False
        if checks[2]['ready']:
            script = prefix/'share/chimaera_ros_bridge/config/gem5_latency.py'
            supported = script.is_file() and "'--cpu-type'" in script.read_text()
        return dict(paths=runtime, checks=checks, ready_native=all(c['ready'] for c in checks[:3]),
            ready_chimaera=all(c['ready'] for c in checks[:7]), supports_cpu_configs=supported,
            can_build=checks[0]['ready'] and checks[-1]['ready'], repo=str(self.repo))

    def _bundle_ready(self, value):
        if not value:
            return False
        try:
            path = Path(value)
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
            if key not in self.bundle_checks:
                with tarfile.open(path) as archive:
                    marker = archive.extractfile('opt/chimaera/latency/latency-runtime.json')
                    metadata = json.loads(marker.read(4096)) if marker else {}
                self.bundle_checks[key] = (isinstance(metadata, dict) and metadata.get('version') == 4
                    and metadata.get('transport_protocol') == 3
                    and isinstance(metadata.get('cpu_types'), list)
                    and all(cpu in metadata['cpu_types'] for cpu in ('kvm', 'timing', 'o3'))
                    and isinstance(metadata.get('workloads'), list)
                    and 'ball_catching' in metadata['workloads'])
            return self.bundle_checks[key]
        except (OSError, KeyError, tarfile.TarError, ValueError):
            return False

    def _environment(self, runtime):
        prefix = Path(runtime['prefix'])
        setup = prefix / 'share/chimaera_ros_bridge/local_setup.bash'
        keys = ['PATH','LD_LIBRARY_PATH','AMENT_PREFIX_PATH','PYTHONPATH','ROS_DISTRO','RMW_IMPLEMENTATION']
        script = 'set -e\nsource /opt/ros/humble/setup.bash\nsource ' + shlex.quote(str(setup)) + '\n'
        script += 'python3 -c ' + shlex.quote('import os,json; print(json.dumps({k:os.environ[k] for k in ' + repr(keys) + ' if k in os.environ}))')
        result = subprocess.run(['bash','-c',script], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError('Cannot source the runtime setup: ' + result.stderr.strip())
        env = dict(os.environ)
        env.update(json.loads(result.stdout.splitlines()[-1]))
        return env

    def _new(self, kind, name, config=None, cases=None, source=None):
        if self.busy() or (self.worker and self.worker.is_alive()):
            raise ValueError('One job can run at a time. Stop the active job or wait for completion.')
        identifier = uuid.uuid4().hex
        directory = self.data / 'sweeps' / identifier
        directory.mkdir()
        job = dict(id=identifier, kind=kind, name=name, config=config, cases=cases or [],
                   status='preparing' if kind == 'prepare' else 'running', created=time.time(),
                   runtime=dict(self.runtime), source=str(source) if source else str(directory), error=None)
        self.jobs[identifier] = job
        self._save(job)
        return job

    def start(self, config):
        plan = plan_sweep(config)
        with self.lock:
            preflight = self.preflight()
            if not preflight['ready_native']:
                raise ValueError('The native runtime is not ready. Check prerequisites, then Build and stage.')
            if 'chimaera' in plan['config']['modes']:
                if not preflight['ready_chimaera']:
                    raise ValueError('Chimaera needs KVM, the gem5 binary, disk, kernel and a staged guest bundle.')
                if not preflight['supports_cpu_configs']:
                    raise ValueError('Rebuild the runtime to enable selectable gem5 configurations.')
                for socket in ('chimaera_g2h.sock','chimaera_h2g.sock','chimaera_time.sock'):
                    if Path('/tmp',socket).exists():
                        raise ValueError('Chimaera transport sockets already exist. Finish the other session and check ownership before retrying.')
            cases = [dict(case, status='pending') for case in plan['cases']]
            job = self._new('sweep', plan['config']['name'], plan['config'], cases)
            self.cancelled.clear()
            self.worker = threading.Thread(target=self._execute, args=(job['id'],), daemon=True)
            self.worker.start()
            return self.job(job['id'])

    def resume(self, identifier, timeout=None):
        with self.lock:
            if self.busy() or (self.worker and self.worker.is_alive()):
                raise ValueError('Wait for the active job and its cleanup to finish before resuming')
            job = self.jobs.get(identifier)
            if not job or job['kind']!='sweep' or job['status'] not in ('failed','stopped','interrupted'):
                raise ValueError('Only failed, stopped or interrupted sweeps can be resumed')
            remaining = [case for case in job['cases'] if case['status']!='complete']
            if not remaining:
                raise ValueError('There are no unfinished cases')
            suggested = max(wall_budget(case['config'])['recommended_timeout'] for case in remaining)
            timeout = suggested if timeout is None else timeout
            if type(timeout) is not int or not 1<=timeout<=86400:
                raise ValueError('The resumed case timeout must be an integer from 1 to 86400 seconds')
            preflight = self.preflight()
            if not preflight['ready_native']:
                raise ValueError('The native runtime is not ready')
            if any(case['config']['mode']=='chimaera' for case in remaining):
                if not preflight['ready_chimaera'] or not preflight['supports_cpu_configs']:
                    raise ValueError('The Chimaera runtime is not ready')
                for name in ('chimaera_g2h.sock','chimaera_h2g.sock','chimaera_time.sock'):
                    if Path('/tmp',name).exists():
                        raise ValueError('Finish the other Chimaera session before resuming')
            # Reuse controls only if the collector and physical clock plugin
            # still match. Deadline changes do not alter the measured workload.
            prefix = Path(self.runtime['prefix'])
            fingerprints = {name:hashlib.sha256((prefix/path).read_bytes()).hexdigest()
                for name,path in [('latency_endpoint','lib/chimaera_ros_bridge/latency_endpoint'),
                                  ('libchimaera_latency_clock.so','lib/libchimaera_latency_clock.so')]}
            for case in job['cases']:
                if case['status']!='complete':
                    continue
                directory = Path(case.get('path',str(Path(job['source'])/case['id'])))
                record = read_json(directory/'run.json', {})
                if record.get('status')!='complete':
                    raise ValueError('A completed case is missing or incomplete; cannot reuse it')
                saved = {Path(path).name:digest for path,digest in record.get('artifact_sha256',{}).items()}
                if any(saved.get(name)!=digest for name,digest in fingerprints.items()):
                    raise ValueError('Collectors changed since the completed cases. Start a new sweep to obtain matching baselines.')
            job.setdefault('resume_history',[]).append(dict(at=time.time(), status=job['status'],
                error=job.get('error'), previous_timeout=job['config']['timeout'], timeout=timeout,
                previous_runtime=job['runtime']))
            for case in remaining:
                previous = Path(case.get('path',str(Path(job['source'])/case['id'])))
                if previous.exists():
                    case.setdefault('attempts',[]).append(dict(path=str(previous),
                        status=case['status'], error=case.get('error'), started=case.get('started'), finished=case.get('finished')))
                    case['path'] = str(Path(job['source'])/(case['id']+f"_attempt_{len(case['attempts'])+1:03d}"))
                case['config']['timeout'] = timeout
                case['wall_budget'] = wall_budget(case['config'])
                case['status'] = 'pending'
                for key in ('error','started','finished'):
                    case.pop(key,None)
            job['config']['timeout'] = timeout
            job.update(status='running', error=None, runtime=dict(self.runtime), resumed=time.time())
            for key in ('finished','report_error'):
                job.pop(key,None)
            self._save(job)
            self.cancelled.clear()
            self.worker = threading.Thread(target=self._execute, args=(identifier,), daemon=True)
            self.worker.start()
            return self.job(identifier)

    def _execute(self, identifier):
        job = self.jobs[identifier]
        sessions = SweepSessions(job['config'].get('single_boot', False) and self.executor is run_case)
        try:
            runtime = job['runtime']
            environment = self._environment(runtime)
            for case in ordered_cases(job['cases'], sessions.enabled):
                if case['status']=='complete':
                    continue
                if self.cancelled.is_set():
                    raise KeyboardInterrupt('Stopped by user')
                with self.lock:
                    case.update(status='running', started=time.time())
                    self._save(job)
                directory = Path(case.get('path',str(Path(job['source']) / case['id'])))
                extra = dict(reuse=sessions.for_case(case['config'])) if sessions.enabled else {}
                self.executor(directory, case['config'], Path(runtime['prefix']), Path(runtime['gem5_root']),
                    Path(runtime['image']), Path(runtime['kernel']), Path(runtime['bundle']) if runtime['bundle'] else None,
                    environment=environment, cancelled=self.cancelled.is_set, **extra)
                with self.lock:
                    case.update(status='complete', finished=time.time())
                    self._save(job)
                self._report(job)
            with self.lock:
                job.update(status='complete', finished=time.time())
        except BaseException as error:
            with self.lock:
                stopped = self.cancelled.is_set() or isinstance(error, KeyboardInterrupt)
                job.update(status='stopped' if stopped else 'failed', error=str(error), finished=time.time())
                for case in job['cases']:
                    if case['status'] == 'running':
                        case.update(status='stopped' if stopped else 'failed', error=str(error), finished=time.time())
        finally:
            sessions.close()
            with self.lock:
                self._save(job)
            try:
                self._report(job)
            except Exception as error:
                with self.lock:
                    job['report_error'] = str(error)
                    self._save(job)

    def _report(self, job):
        if job['kind'] == 'prepare':
            return
        report = make_report(Path(job['source']), write_outputs=job['kind'] != 'import')
        directories = {str(case['path'] if 'path' in case else Path(job['source'])/case['id']): case['id'] for case in job['cases']}
        by_run = {}
        for directory, case_id in directories.items():
            data = read_json(Path(directory)/'run.json', {})
            if data.get('run_id'):
                by_run[data['run_id']] = case_id
        for run in report['runs']:
            run['case_id'] = by_run.get(run['config']['run_id'])
        with self.lock:
            self.reports[job['id']] = report
            write_json(self.data/'sweeps'/job['id']/'workbench-report.json', report)

    def report(self, identifier):
        self.job(identifier)
        with self.lock:
            return self.reports.get(identifier) or read_json(self.data/'sweeps'/identifier/'workbench-report.json',
                dict(runs=[], comparisons=[], excluded=[]))

    def case_directory(self, identifier, case_id):
        job = self.job(identifier)
        case = next((item for item in job['cases'] if item['id'] == case_id), None)
        if not case:
            raise ValueError('Case not found')
        return Path(case.get('path', str(Path(job['source'])/case_id)))

    def stop(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job['status'] not in ACTIVE:
                raise ValueError('No active job to stop')
            self.cancelled.set()
            job['status'] = 'stopping'
            self._save(job)
            return self.job(identifier)

    def prepare(self, build=True):
        with self.lock:
            preflight = self.preflight()
            if build and not preflight['can_build']:
                raise ValueError('Install ROS Humble, colcon and CMake before building the runtime')
            if not build and not preflight['ready_native']:
                raise ValueError('Select a built runtime prefix before staging it')
            job = self._new('prepare', 'Build and stage runtime' if build else 'Stage existing runtime')
            self.cancelled.clear()
            self.worker = threading.Thread(target=self._prepare, args=(job['id'], build), daemon=True)
            self.worker.start()
            return self.job(job['id'])

    def _prepare(self, identifier, build):
        job, process = self.jobs[identifier], None
        directory = self.data/'sweeps'/identifier
        outcome = dict(status='complete')
        try:
            runtime = dict(job['runtime'])
            if build:
                command = ['colcon','--log-base',str(self.data/'build-logs'),'build',
                    '--base-paths',str(self.repo/'ros/chimaera_ros_ws/src'),
                    '--build-base',str(self.data/'build'),'--install-base',str(self.data/'install'),
                    '--packages-select','chimaera_ros_bridge','--executor','sequential',
                    '--event-handlers','console_direct+','--cmake-args',
                    '-DBUILD_LATENCY_BENCHMARK=ON','-DBUILD_GAZEBO_BRIDGE=ON',
                    '-DBUILD_GUEST_BRIDGE=ON','-DBUILD_TESTING=OFF','-DCMAKE_BUILD_TYPE=Release']
                script = ('set -e\nsource /opt/ros/humble/setup.bash\n'
                    'export CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS="-j2 -l2"\nexec ' + shlex.join(command))
                write_json(directory/'commands.json', command)
                with (directory/'prepare.log').open('w') as log:
                    process = subprocess.Popen(['bash','-c',script], cwd=self.repo,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    while process.poll() is None:
                        if self.cancelled.wait(.1):
                            raise KeyboardInterrupt('Build stopped by user')
                    if process.returncode:
                        raise ValueError('Runtime build failed. Open the preparation log for the compiler output.')
                runtime['prefix'] = str(self.data/'install/chimaera_ros_bridge')
            if self.cancelled.is_set():
                raise KeyboardInterrupt('Staging stopped by user')
            bundle = directory/'guest.tar.gz'
            stage_guest(directory/'guest-root', Path(runtime['prefix']), bundle)
            if self.cancelled.is_set():
                raise KeyboardInterrupt('Staging stopped by user')
            runtime['bundle'] = str(bundle)
            with self.lock:
                self.runtime = runtime
                write_json(self.data/'runtime.json', runtime)
        except BaseException as error:
            outcome = dict(status='stopped' if self.cancelled.is_set() else 'failed', error=str(error))
        finally:
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    pass
            with self.lock:
                job.update(outcome, finished=time.time())
                self._save(job)

    def import_results(self, value):
        source = Path(value).expanduser().resolve()
        if not source.is_dir() or source == Path('/'):
            raise ValueError('Choose a directory containing benchmark run.json and events.csv files')
        report = make_report(source, write_outputs=False)
        if not report['runs']:
            raise ValueError('No complete latency benchmark runs were found')
        cases = []
        for path in sorted(source.rglob('run.json')):
            config = read_json(path)
            if config.get('mode') in MODES:
                cases.append(dict(id=f'case_{len(cases)+1:04d}', config=config, status=config['status'], path=str(path.parent)))
        first = report['runs'][0]['config']
        config = copy.deepcopy(DEFAULT)
        config.update({key:first[key] for key in ('duration','warmup','drain','physics_step_ns','depth','reliability','timeout','workload','ball_catching') if key in first})
        config['name'] = source.name
        for key in ('payload_bytes','rate_hz','interval_us','poll_us'):
            config[key] = list(dict.fromkeys(case['config'][key] for case in cases))
        profiles = {}
        for case in cases:
            raw = case['config']
            if raw['mode'] == 'chimaera':
                name = raw.get('gem5_name',raw.get('cpu','kvm').upper())
                profiles[name] = dict(HARDWARE, **{key:raw[key] for key in HARDWARE if key in raw}, name=name)
        config['gem5_configs'] = list(profiles.values()) or config['gem5_configs']
        with self.lock:
            job = self._new('import',source.name,config,cases,source)
            job.update(status='complete', finished=time.time())
            self._save(job)
        self._report(job)
        return self.job(job['id'])

    def state(self):
        with self.lock:
            jobs = copy.deepcopy(list(self.jobs.values()))
        for job in jobs:
            job['completed'] = sum(case['status']=='complete' for case in job['cases'])
            job['total'] = len(job['cases'])
            running = next((case for case in job['cases'] if case['status']=='running'), None)
            if running:
                directory = Path(running.get('path',str(Path(job['source'])/running['id'])))
                value = clock_value(directory/('logical-clock.bin' if running['config']['mode']=='chimaera' else 'physics-clock.bin'))
                config = running['config']
                elapsed = value/1e9 if value is not None else None
                phase = ('Starting Gazebo / booting guest' if elapsed is None else 'Warming up' if elapsed<config['warmup']
                    else 'Measuring' if elapsed<config['warmup']+config['duration']
                    else 'Draining messages' if elapsed<config['warmup']+config['duration']+config['drain'] else 'Exporting records')
                if elapsed is None and config['mode'] == 'chimaera':
                    phase = startup_status(directory)['description']
                job['progress'] = dict(case=running['id'], phase=phase, simulated_seconds=elapsed)
            if job['kind']=='sweep' and job['status'] in ('failed','stopped','interrupted'):
                remaining = [case for case in job['cases'] if case['status']!='complete']
                if remaining:
                    job['resume_timeout'] = max(wall_budget(case['config'])['recommended_timeout'] for case in remaining)
                failed = next((case for case in job['cases'] if case['status']=='failed'),None)
                if failed and job.get('error')=='Driver failed or guest export incomplete; see driver.log':
                    directory = Path(failed.get('path',str(Path(job['source'])/failed['id'])))
                    record = read_json(directory/'run.json', {})
                    job['error'] = driver_failure(directory,failed['config'],record.get('wall_seconds',0))
        return dict(jobs=sorted(jobs,key=lambda job:job['created'],reverse=True),runtime=self.preflight(),time=time.time())

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                if job['status'] in ACTIVE:
                    self.cancelled.set()
        if self.worker:
            self.worker.join(timeout=15)


def handler(workbench, web):
    class Handler(BaseHTTPRequestHandler):
        def json(self,value,code=200):
            body=json.dumps(value,allow_nan=False).encode()
            self.send_bytes(body,'application/json',code)

        def send_bytes(self,body,kind,code=200,name=None):
            self.send_response(code)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if name:
                self.send_header('Content-Disposition',f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host=self.headers.get('Host','')
            addresses = {'localhost', '127.0.0.1', self.connection.getsockname()[0]}
            authorities = {f'{address}:{self.server.server_port}' for address in addresses}
            public_address = getattr(self.server, 'public_address', None)
            if isinstance(public_address, str) and public_address:
                authorities.add(public_address)
            if host not in authorities:
                raise ValueError('Use the workbench IP address and port')
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{host}':
                raise ValueError('Cross-origin requests are not accepted')

        def do_GET(self):
            try:
                self.allowed()
                url=urlsplit(self.path)
                parts=unquote(url.path).strip('/').split('/')
                query=parse_qs(url.query)
                if parts==['api','state']:
                    self.json(workbench.state())
                elif parts==['api','defaults']:
                    self.json(DEFAULT)
                elif len(parts)==4 and parts[:2]==['api','jobs']:
                    identifier,action=parts[2:]
                    job=workbench.job(identifier)
                    if action=='report':
                        self.json(workbench.report(identifier))
                    elif action in ('samples','logs','events'):
                        case_id=query.get('case',[''])[0]
                        if action=='logs' and job['kind']=='prepare':
                            self.json(tail(workbench.data/'sweeps'/identifier/'prepare.log'))
                            return
                        directory=workbench.case_directory(identifier,case_id)
                        if action=='samples':
                            self.json(samples(directory,query.get('direction',['host_to_guest'])[0], query.get('stream',[None])[0]))
                        elif action=='logs':
                            stream=query.get('stream',['driver'])[0]
                            if stream not in STREAMS:
                                raise ValueError('Unknown log stream')
                            self.json(tail(directory/STREAMS[stream]))
                        else:
                            path=directory/'events.csv'
                            self.send_response(200)
                            self.send_header('Content-Type','text/csv')
                            self.send_header('Content-Disposition','attachment; filename="events.csv"')
                            self.send_header('Content-Length',str(path.stat().st_size))
                            self.end_headers()
                            with path.open('rb') as stream:
                                shutil.copyfileobj(stream,self.wfile)
                    elif action=='export':
                        report=workbench.report(identifier)
                        if query.get('format',['json'])[0]=='csv':
                            csv_output=io.StringIO()
                            writer=csv.writer(csv_output)
                            writer.writerow(['case','mode','gem5_configuration','direction','stream','payload_bytes','rate_hz','interval_us','poll_us','stream_rate_hz','serialized_bytes','received','published','mean_ms','p50_ms','p95_ms','p99_ms','max_ms'])
                            for run in report['runs']:
                                config=run['config']
                                for direction,stream,metrics in measurements(run):
                                    writer.writerow([run['case_id'],config['mode'],config.get('gem5_name',config.get('cpu','')),direction,stream,
                                        *[config[key] for key in ('payload_bytes','rate_hz','interval_us','poll_us')],
                                        metrics.get('rate_hz',config['rate_hz']),metrics.get('serialized_bytes'),
                                        metrics['received'],metrics['published'],
                                        *[metrics['latency'][key] for key in ('mean_ms','p50_ms','p95_ms','p99_ms','max_ms')]])
                            self.send_bytes(csv_output.getvalue().encode(),'text/csv',name='latency-results.csv')
                        else:
                            self.send_bytes(json.dumps(report,allow_nan=False,indent=2).encode(),'application/json',name='latency-results.json')
                    else:
                        self.json(dict(error='Not found'),404)
                elif parts and parts[0]=='api':
                    self.json(dict(error='Not found'),404)
                else:
                    relative='index.html' if url.path=='/' else unquote(url.path).lstrip('/')
                    path=(web/relative).resolve()
                    if not path.is_relative_to(web) or not path.is_file():
                        self.json(dict(error='Not found'),404)
                    else:
                        self.send_bytes(path.read_bytes(),mimetypes.guess_type(path)[0] or 'application/octet-stream')
            except (BrokenPipeError,ConnectionResetError):
                pass
            except (ValueError,OSError,KeyError) as error:
                self.json(dict(error=str(error)),400)

        def do_POST(self):
            try:
                self.allowed()
                if self.headers.get('X-Chimaera-Request')!='1':
                    raise ValueError('Missing workbench request header')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024**2:
                    raise ValueError('Request must contain at most 1 MiB of JSON')
                value=json.loads(self.rfile.read(length))
                if self.path=='/api/plan':
                    self.json(plan_sweep(value))
                elif self.path=='/api/start':
                    self.json(workbench.start(value),202)
                elif self.path=='/api/prepare':
                    build=value.get('build',True)
                    if type(build) is not bool:
                        raise ValueError('build must be true or false')
                    self.json(workbench.prepare(build),202)
                elif self.path=='/api/runtime':
                    self.json(workbench.set_runtime(value))
                elif self.path=='/api/stop':
                    self.json(workbench.stop(value['id']))
                elif self.path=='/api/resume':
                    self.json(workbench.resume(value['id'],value.get('timeout')),202)
                elif self.path=='/api/import':
                    self.json(workbench.import_results(value['path']))
                else:
                    self.json(dict(error='Not found'),404)
            except (ValueError,KeyError,OSError,TypeError,AttributeError) as error:
                self.json(dict(error=str(error)),400)

        def log_message(self,*_):
            pass
    return Handler


def serve(repo, data=None, runtime_prefix=None, port=8787, host='127.0.0.1', public_address=None):
    repo=Path(repo).resolve()
    data=Path(data or repo/'.chimaera-latency').resolve()
    data.mkdir(parents=True, exist_ok=True)
    lock=(data/'workbench.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise
    workbench=Workbench(repo,data,runtime_prefix)
    web=Path(__file__).resolve().parents[1]/'latency_web'
    if not web.is_dir():
        web=Path(runtime_prefix or workbench.runtime['prefix'])/'share/chimaera_ros_bridge/latency_web'
    if not web.is_dir():
        raise ValueError('Workbench assets are missing; rebuild with BUILD_LATENCY_BENCHMARK=ON')
    server=ThreadingHTTPServer((host,port),handler(workbench,web.resolve()))
    server.public_address = public_address
    server.timeout=.5
    stopping=threading.Event()
    previous={sig:signal.signal(sig,lambda *_:stopping.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    print(f'Chimaera latency workbench: http://{host}:{server.server_port}',flush=True)
    try:
        while not stopping.is_set():
            server.handle_request()
    finally:
        workbench.close()
        server.server_close()
        lock.close()
        for sig,callback in previous.items():
            signal.signal(sig,callback)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=Path.cwd())
    parser.add_argument('--data-root',type=Path)
    parser.add_argument('--runtime-prefix',type=Path)
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--host',default='127.0.0.1',help='Bind address; use the host LAN IP for LAN access')
    args=parser.parse_args()
    serve(args.repo_root,args.data_root,args.runtime_prefix,args.port,args.host)


if __name__=='__main__':
    main()
