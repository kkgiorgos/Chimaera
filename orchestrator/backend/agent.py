"""Durable local supervisor. Workers are detached sibling Docker containers."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import uuid

from common import Store, atomic_json, identifier, run, case_log, tail_file

ROOT = Path(os.environ.get('CHIMAERA_REPO', '/repo'))
DATA = ROOT / '.chimaera-orchestrator'
IMAGE = 'chimaera-supervisor:local'


def memory_bytes(value):
    """Parse configured Docker sizes and human-readable docker stats sizes."""
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(b|[kmg](?:i?b)?)?\s*', value, re.I)
    if not match:
        raise ValueError('Invalid memory size: ' + value)
    unit = (match[2] or '').lower()
    power = {'': 0, 'b': 0, 'k': 1, 'm': 2, 'g': 3}.get(unit)
    if power is None:
        power = {'k': 1, 'm': 2, 'g': 3}[unit[0]]
    base = 1024 if len(unit) <= 1 or 'i' in unit else 1000
    return int(float(match[1]) * base ** power)


class Agent:
    def __init__(self, root=ROOT, data=DATA):
        self.root, self.data = Path(root), Path(data)
        self.store = Store(self.data / 'agent.sqlite')
        self.lock = threading.RLock()

    def rpc(self, method, **params):
        if method not in {'snapshot', 'prepare', 'start', 'stop', 'stop_case', 'logs', 'cleanup', 'clean_assets', 'storage', 'remember_image', 'asset_destination', 'commit_assets', 'release', 'release_attempt', 'reset_prepare'}:
            raise ValueError('Unknown supervisor operation')
        with self.lock:
            return getattr(self, method)(**params)

    def directory(self, experiment):
        return self.data / 'experiments' / identifier(experiment)

    def prepare(self, experiment, config, cpus, memory, build_jobs, source_identity, disk='', kernel='', calibration=None, generation=0):
        self.claim(experiment)
        self.check_generation(experiment, generation)
        directory = self.directory(experiment)
        key = experiment + '-prepare'
        existing = self.store.get('tasks', key)
        if existing:
            return existing
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / 'request.json', dict(experiment=experiment, config=config, cpus=cpus,
            memory=memory, build_jobs=build_jobs, disk=disk, kernel=kernel, calibration=calibration,
            source_identity=source_identity,
            readiness_checkpoint=config.get('fixed', {}).get('transport_backend', 'm5') != 'dma'))
        command = ['docker', 'run', '-d', '--name', 'chimaera-' + key,
            '--label', 'chimaera.experiment=' + experiment,
            '--user', f'{os.getuid()}:{os.getgid()}', '--group-add', str(Path('/var/run/docker.sock').stat().st_gid),
            '-v', '/var/run/docker.sock:/var/run/docker.sock', '-v', f'{self.root}:{self.root}',
            '-e', 'CHIMAERA_REPO=' + str(self.root), '-e', 'HOME=/tmp',
            '-e', 'CHIMAERA_OPERATION=' + experiment,
            *(['--device', '/dev/kvm'] if not calibration else []), IMAGE,
            'python3', '/app/pipeline.py', str(directory)]
        return self.launch(key, experiment, 'prepare', command, directory)

    def asset_destination(self, experiment):
        self.claim(experiment)
        path = self.directory(experiment) / 'shared-assets.incoming'
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return str(path)

    def commit_assets(self, experiment, expected):
        from checkpoints import unpack
        self.claim(experiment)
        directory = self.directory(experiment)
        incoming = directory / 'shared-assets.incoming'
        destination = directory / 'shared-assets'
        # Validate in place before publishing. A failed transfer is never ready.
        unpack(incoming.parent, '', expected, destination=incoming)
        if destination.exists():
            shutil.rmtree(destination)
        incoming.rename(destination)
        return True

    def claim(self, experiment):
        self.directory(experiment)
        if self.store.get('released', experiment):
            raise ValueError('Experiment inputs were released; create a new experiment')
        active = self.store.get('lifecycle', 'active')
        legacy = any(p.is_dir() and p.name != experiment and not self.store.get('released', p.name)
                     for p in (self.data / 'experiments').glob('*'))
        if (active and active != experiment) or legacy:
            raise ValueError('Finalize the existing experiment before preparing another')
        self.store.put('lifecycle', 'active', experiment)

    def check_generation(self, experiment, generation):
        if generation <= self.store.get('stopped', experiment, -1):
            raise ValueError('Experiment was stopped; resume before dispatching')

    def reset_prepare(self, experiment):
        key = experiment + '-prepare'
        task = self.store.get('tasks', key)
        if task and task['status'] in ('accepted', 'running'):
            raise ValueError('Preparation is still running')
        if task:
            try:
                run(['docker', 'rm', task['container']], timeout=15)
            except RuntimeError as error:
                if 'no such' not in str(error).lower():
                    raise
            self.store.delete('tasks', key)
        directory = self.directory(experiment)
        if directory.exists():
            archive = directory / 'preparation-failures' / uuid.uuid4().hex[:8]
            archive.mkdir(parents=True)
            for name in ('ready.json', 'stage.json', 'experiment.json',
                         'request.json', 'worker.log', 'preparation-report'):
                path = directory / name
                if path.exists():
                    path.rename(archive / name)
            for name in ('inputs', 'export-assets', 'checkpoint-image', 'readiness-assets'):
                shutil.rmtree(directory / name, ignore_errors=True)
        return True

    def launch(self, key, experiment, kind, command, output, **metadata):
        name = command[command.index('--name') + 1]
        task = dict(id=key, experiment=experiment, kind=kind, container=name,
                    command=command, output=str(output), status='accepted', started=time.time(), **metadata)
        # Persist before Docker. Recovery inspects the same name before ever reissuing create.
        self.store.put('tasks', key, task)
        self.ensure_started(task)
        return self.store.get('tasks', key)

    def ensure_started(self, task):
        if task['kind'] == 'case' and self.store.get('stopped_attempts', task['id']):
            self.stop_case(task['experiment'], task['case'], task['id'])
            return
        try:
            raw = run(['docker', 'inspect', task['container']], text=True, timeout=10)
        except RuntimeError as error:
            if 'no such' not in str(error).lower():
                raise
            raw = None
        if not raw:
            try:
                run(task['command'], timeout=30)
            except RuntimeError as error:
                # Check for an accepted container before recording a concrete Docker failure.
                try:
                    run(['docker', 'inspect', task['container']], timeout=10)
                except RuntimeError:
                    task.update(status='failed', error=str(error), finished=time.time())
                self.store.put('tasks', task['id'], task)
                return
        task['status'] = 'running'
        self.store.put('tasks', task['id'], task)

    def start(self, experiment, case, attempt, cpus, memory, generation=0):
        self.claim(experiment)
        self.check_generation(experiment, generation)
        identifier(case)
        key = identifier(attempt)
        if self.store.get('stopped_attempts', key):
            raise ValueError('Attempt was stopped; retry with a fresh attempt')
        existing = self.store.get('tasks', key)
        if existing:
            return existing
        directory = self.directory(experiment)
        if not (directory / 'ready.json').is_file():
            raise ValueError('Machine preparation has not completed')
        spec = importlib.util.spec_from_file_location('shared_runner', self.root / 'containers/run.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        config = runner.load(directory / 'inputs/runner.json')
        config.update(cpus=cpus, memory=memory)
        job = next(job for job in config['jobs'] if job['name'] == case)
        output = directory / 'attempts' / key
        output.mkdir(parents=True, exist_ok=True)
        command = runner.command(config, job, output, config['image'], 'chimaera-' + key)
        # Orchestrated cases may exceed their RAM budget without losing a run.
        # Keep standalone runner hard limits unchanged.
        command[command.index('--memory')] = '--memory-reservation'
        swap = command.index('--memory-swap')
        del command[swap:swap + 2]
        command.remove('--rm')
        command[2:2] = ['-d', '--label', 'chimaera.experiment=' + experiment]
        return self.launch(key, experiment, 'case', command, output, case=case,
                           memory_budget_bytes=memory_bytes(memory))

    def reconcile(self):
        for task in self.store.all('tasks'):
            if task['status'] == 'accepted':
                self.ensure_started(task)
            if task['status'] != 'running':
                if task['status'] in {'completed', 'failed', 'stopped'}:
                    self.cleanup_task_container(task)
                continue
            try:
                state = json.loads(run(['docker', 'inspect', task['container']], text=True, timeout=10))[0]['State']
            except RuntimeError as error:
                if 'no such' not in str(error).lower():
                    raise
                task.update(status='failed', error='Container missing: ' + str(error),
                            finished=time.time(), container_removed=True)
                self.store.put('tasks', task['id'], task)
                continue
            if state['Running']:
                continue
            output = Path(task['output'])
            task.update(status='completed' if state['ExitCode'] == 0 else 'failed',
                        exit_code=state['ExitCode'], oom=state.get('OOMKilled', False), finished=time.time())
            try:
                self.save_worker_log(task)
            except (OSError, subprocess.SubprocessError) as error:
                task['log_error'] = str(error)
            if task['kind'] == 'case' and task['status'] == 'completed':
                try:
                    manifest = json.loads((output / 'suite/experiment_manifest.json').read_text())
                    point = next(p for p in manifest['points'] if p['id'] == task['case'])
                    if point['status'] != 'complete' or not point['trials']:
                        raise ValueError('No completed trial')
                except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
                    task.update(status='failed', error='Case exited without a completed result: ' + str(error))
            if task['kind'] == 'prepare':
                progress = self.preparation_progress(task)
                if task['status'] == 'failed':
                    error = progress.get('reason') or self.preparation_log_error(task)
                    if error:
                        task['error'] = error
                if task['status'] == 'completed':
                    try:
                        ready = json.loads((output / 'ready.json').read_text())
                        if not ready.get('records') or not ready.get('provenance'):
                            raise ValueError('Missing calibration or provenance')
                    except (OSError, ValueError, AttributeError) as error:
                        task.update(status='failed', error='Preparation exited without valid ready.json: ' + str(error))
                self.preparation_report(task)
            self.store.put('tasks', task['id'], task)
            self.cleanup_task_container(task)

    def save_worker_log(self, task):
        output = Path(task['output'])
        output.mkdir(parents=True, exist_ok=True)
        temporary = output / 'worker.log.tmp'
        try:
            with temporary.open('wb') as stream:
                subprocess.run(['docker', 'logs', task['container']], stdout=stream,
                               stderr=subprocess.STDOUT, timeout=30, check=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(output / 'worker.log')
        finally:
            temporary.unlink(missing_ok=True)
        task['worker_log_saved'] = True
        task.pop('log_error', None)

    def cleanup_task_container(self, task):
        """Keep durable logs before removing a terminal task's container; retry on polls."""
        if task.get('container_removed') or task['status'] not in {'completed', 'failed', 'stopped'}:
            return
        try:
            if not task.get('worker_log_saved'):
                self.save_worker_log(task)
                if task['kind'] == 'prepare':
                    self.preparation_report(task)
                # Persist before rm so recovery never depends on a removed container.
                self.store.put('tasks', task['id'], task)
            try:
                # Never force removal: an unexpectedly running container is preserved.
                run(['docker', 'rm', task['container']], timeout=30)
            except RuntimeError as error:
                if 'no such' not in str(error).lower():
                    raise
            task['container_removed'] = True
            task.pop('container_cleanup_error', None)
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            task['container_cleanup_error'] = str(error)
        self.store.put('tasks', task['id'], task)

    def preparation_progress(self, task):
        scripts = str(self.root / 'ros/ball_catching_ws/scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from watch_memory_suite import preparation_snapshot
        try:
            return preparation_snapshot(Path(task['output']) / 'inputs/prepared')
        except (OSError, ValueError, KeyError, TypeError):
            return dict(phase='Waiting for preparation progress records')

    def preparation_log_error(self, task):
        try:
            log = tail_file(Path(task['output']) / 'worker.log')
        except OSError:
            return None
        # The innermost CLI reports its cause before wrappers report the
        # CalledProcessError. Retain that cause for pre-checkpoint failures too.
        errors = re.findall(r'^[\w.-]+\.py: error: [^\r\n]+', log, re.MULTILINE)
        return errors[0] if errors else None

    def preparation_report(self, task):
        output = Path(task['output'])
        try:
            report = output / 'preparation-report'
            report.mkdir(parents=True, exist_ok=True)
            for name in ('worker.log', 'request.json', 'ready.json', 'stage.json', 'experiment.json',
                         'inputs/inputs.json', 'inputs/runner.json', 'inputs/plan.json',
                         'inputs/prepared/preparation.json', 'inputs/prepared/checkpoints/progress.json'):
                source = output / name
                if source.is_file():
                    destination = report / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            # Keep small readiness diagnostics with the collected report. RAM,
            # disks and replay streams stay on the worker and in the final image.
            for directory in (output / 'inputs/prepared/checkpoints').glob('*'):
                if not directory.is_dir():
                    continue
                for name in ('launch.log', 'timing.csv', 'readiness.csv', 'checkpoint-stage.json',
                             'checkpoint.json', 'execution.json', 'checkpoint-host-state',
                             'checkpoint-ready', 'gem5/board.pc.com_1.device', 'gem5/cpu_roi.json'):
                    source = directory / name
                    if source.is_file():
                        destination = report / 'checkpoints' / directory.name / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
            if task['status'] in ('failed', 'stopped'):
                atomic_json(report / 'failure.json', task)
        except OSError as error:
            task['log_error'] = str(error)

    def snapshot(self):
        self.reconcile()
        tasks = self.store.all('tasks')
        scripts = str(self.root / 'ros/ball_catching_ws/scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from watch_memory_suite import snapshot
        for task in tasks:
            if task.get('output_removed'):
                continue
            if task['kind'] == 'case':
                try:
                    task['progress'] = snapshot(Path(task['output']) / 'suite')
                except (OSError, ValueError, KeyError, TypeError):
                    task['progress'] = dict(phase='Waiting for progress records')
            elif task['status'] == 'completed':
                path = Path(task['output']) / 'ready.json'
                try:
                    task['ready'] = json.loads(path.read_text()) if path.exists() else None
                except (OSError, ValueError, AttributeError, TypeError) as error:
                    task.update(status='failed', error='Cannot read preparation result: ' + str(error))
            if task['kind'] == 'prepare':
                path = Path(task['output']) / 'stage.json'
                try:
                    task['phase'] = json.loads(path.read_text()).get('phase') if path.exists() else 'Starting preparation'
                except (OSError, ValueError) as error:
                    task['phase'] = 'Cannot read preparation progress: ' + str(error)
                progress = self.preparation_progress(task)
                if task.get('phase') in ('Calibrating and creating readiness checkpoints',
                        'Validating shared calibration and readiness checkpoints') and progress.get('phase'):
                    task['phase'] = progress['phase']
                if task.get('started') is not None:
                    progress['elapsed_seconds'] = max(0, task.get('finished', time.time()) - task['started'])
                task['progress'] = progress
                if task['status'] == 'failed':
                    task['phase'] = 'Preparation failed'
                    error = progress.get('reason') or task.get('error') or self.preparation_log_error(task)
                    if error:
                        task['error'] = error
                report = Path(task['output']) / 'preparation-report'
                if task['status'] in ('failed', 'stopped') and not report.exists():
                    self.preparation_report(task)
        containers = [t['container'] for t in tasks if t['status'] == 'running']
        try:
            stats = run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *containers], text=True, timeout=15) if containers else ''
        except (RuntimeError, OSError, subprocess.SubprocessError):
            stats = ''
        stats = [json.loads(line) for line in stats.splitlines()]
        by_name = {item['Name']: item for item in stats}
        for task in tasks:
            sample = by_name.get(task['container'])
            if task['kind'] != 'case' or not sample or not task.get('memory_budget_bytes'):
                continue
            try:
                used = memory_bytes(sample['MemUsage'].split('/', 1)[0])
            except (ValueError, KeyError):
                continue
            task['memory_peak_bytes'] = max(task.get('memory_peak_bytes', 0), used)
            if task['memory_peak_bytes'] > task['memory_budget_bytes']:
                task['memory_warning'] = (
                    f"RAM budget exceeded: observed {task['memory_peak_bytes'] / 1024**3:.2f} GiB "
                    f"against {task['memory_budget_bytes'] / 1024**3:.2f} GiB configured. "
                    'Worker may continue above budget; increase RAM per worker or reduce '
                    'worker count for future runs.')
            self.store.put('tasks', task['id'], task)
        return dict(tasks=tasks, stats=stats,
            host=dict(cpus=os.cpu_count(), load=os.getloadavg(), memory=Path('/proc/meminfo').read_text()), time=time.time())

    def stop_case(self, experiment, case, attempt):
        directory = self.directory(experiment) / 'attempts' / identifier(attempt)
        identifier(case)
        task = self.store.get('tasks', attempt)
        if task and (task['experiment'] != experiment or task['kind'] != 'case' or task['case'] != case):
            raise ValueError('Attempt does not belong to this case')
        # Reject a start RPC arriving after this stop, including after a restart.
        self.store.put('stopped_attempts', attempt, True)
        if not task:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'worker.log').write_text('Stopped before the worker was launched.\n')
            task = dict(id=attempt, experiment=experiment, case=case, kind='case',
                container='chimaera-' + attempt, output=str(directory),
                status='stopped', finished=time.time(), worker_log_saved=True, container_removed=True)
        elif task['status'] in {'accepted', 'running'}:
            try:
                state = json.loads(run(['docker', 'inspect', task['container']], text=True, timeout=10))[0]['State']
            except RuntimeError as error:
                if 'no such' not in str(error).lower():
                    raise
                state = None
            if state and not state['Running'] and task['status'] == 'running':
                # Preserve a result that finished before the stop reached us.
                self.reconcile()
                return self.store.get('tasks', attempt)
            if state:
                try:
                    run(['docker', 'kill', task['container']], timeout=30)
                except RuntimeError:
                    state = json.loads(run(['docker', 'inspect', task['container']], text=True, timeout=10))[0]['State']
                    if state['Running']:
                        raise
                    self.reconcile()
                    return self.store.get('tasks', attempt)
                state = json.loads(run(['docker', 'inspect', task['container']], text=True, timeout=10))[0]['State']
                if state['Running']:
                    raise RuntimeError('Worker is still running after stop')
            task.update(status='stopped', finished=time.time())
            if state is None:
                task['container_removed'] = True
            directory.mkdir(parents=True, exist_ok=True)
            try:
                self.save_worker_log(task)
            except (OSError, subprocess.SubprocessError) as error:
                task['log_error'] = str(error)
        self.store.put('tasks', attempt, task)
        self.cleanup_task_container(task)
        return task

    def stop(self, experiment, generation=0):
        self.directory(experiment)
        self.store.put('stopped', experiment, max(generation, self.store.get('stopped', experiment, -1)))
        self.reconcile()
        # Include preparation containers launched by the helper, not just case workers.
        names = run(['docker', 'ps', '-q', '--filter', 'label=chimaera.experiment=' + experiment], text=True).split()
        if names:
            # A container can finish between ps and kill; inspect reconciles that race.
            subprocess.run(['docker', 'kill', *names], capture_output=True, timeout=30)
        for task in self.store.all('tasks'):
            if task['experiment'] == experiment and task['status'] in ('accepted', 'running'):
                try:
                    state = json.loads(run(['docker', 'inspect', task['container']], text=True, timeout=10))[0]['State']
                except RuntimeError as error:
                    if 'no such' not in str(error).lower():
                        raise
                    task['container_removed'] = True
                else:
                    if state['Running']:
                        raise RuntimeError('Worker is still running after stop')
                task.update(status='stopped', finished=time.time())
                try:
                    self.save_worker_log(task)
                except (OSError, subprocess.SubprocessError) as error:
                    task['log_error'] = str(error)
                self.store.put('tasks', task['id'], task)
        return self.snapshot()

    def logs(self, task, stream='worker'):
        record = self.store.get('tasks', identifier(task))
        if not record:
            raise ValueError('Unknown task')
        if stream != 'worker':
            return case_log(record['output'], stream)
        if record.get('error') and record['status'] == 'failed':
            return record['error']
        log = Path(record['output']) / 'worker.log'
        if record['status'] in {'completed', 'failed', 'stopped'} and log.is_file():
            return '\n'.join(tail_file(log).splitlines()[-250:])
        result = subprocess.run(['docker', 'logs', '--tail', '250', record['container']],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15, check=True)
        return result.stdout.decode(errors='replace')[-100000:]

    def cleanup(self, experiment):
        tasks = [t for t in self.store.all('tasks') if t['experiment'] == experiment]
        if any(t['status'] in ('accepted', 'running') for t in tasks):
            raise ValueError('Stop all tasks before remote cleanup')
        running = run(['docker', 'ps', '-q', '--filter', 'label=chimaera.experiment=' + experiment], text=True).split()
        if running:
            raise ValueError('Preparation or worker containers are still running')
        containers = run(['docker', 'ps', '-aq', '--filter', 'label=chimaera.experiment=' + experiment], text=True).split()
        if containers:
            run(['docker', 'rm', *containers], timeout=30)
        for task in tasks:
            self.store.delete('tasks', task['id'])
        directory = self.directory(experiment)
        if directory.exists():
            shutil.rmtree(directory)
        self.store.put('released', experiment, True)
        if self.store.get('lifecycle', 'active') == experiment:
            self.store.delete('lifecycle', 'active')
        return True

    def release_attempt(self, experiment, attempt):
        task = self.store.get('tasks', identifier(attempt))
        if not task:
            return True
        if task['experiment'] != experiment or task['kind'] != 'case':
            raise ValueError('Attempt does not belong to this experiment')
        if task['status'] not in {'completed', 'failed', 'stopped'}:
            raise ValueError('Attempt is still running')
        self.cleanup_task_container(task)
        if not task.get('container_removed'):
            raise ValueError('Attempt container has not been removed')
        output = self.directory(experiment) / 'attempts' / identifier(attempt)
        if output.exists():
            shutil.rmtree(output)
        task['output_removed'] = True
        self.store.put('tasks', attempt, task)
        return True

    def release(self, experiment, keep_paths=()):
        # A durable tombstone rejects delayed preparation/transfer/start RPCs.
        self.store.put('released', identifier(experiment), True)
        self.cleanup(experiment)
        if self.store.get('lifecycle', 'active') == experiment:
            self.store.delete('lifecycle', 'active')
        from assets import clean_unused
        result = clean_unused(self.data, keep_paths)
        from images import prune
        prune()
        return result

    def storage(self, keep_paths=()):
        from assets import allocated_bytes, asset_inventory, filesystem_usage
        return dict(filesystem=filesystem_usage(self.data), assets=asset_inventory(self.data, keep_paths),
                    experiments=[dict(id=p.name, bytes=allocated_bytes(p))
                                 for p in (self.data / 'experiments').glob('*') if p.is_dir() and not p.is_symlink()])

    def clean_assets(self, keep_paths=(), asset_ids=None):
        from assets import clean_unused
        if any(t['status'] in ('accepted', 'running') for t in self.store.all('tasks')):
            raise ValueError('Wait for machine tasks to stop before cleaning unused assets')
        return clean_unused(self.data, keep_paths, asset_ids)

    def remember_image(self, image, current=False):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('Invalid Docker image ID')
        atomic_json(self.data / 'assets' / ('image-' + image.removeprefix('sha256:') + '.json'),
                    dict(image_id=image))
        if current:
            atomic_json(self.data / 'assets/current-images.json',
                        {'chimaera:orchestrator-app': dict(id=image, key='imported')})
        return True


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('mode', choices=['serve', 'rpc'])
    args = cli.parse_args()
    if args.mode == 'rpc':
        request = urllib.request.Request('http://127.0.0.1:9091', data=sys.stdin.buffer.read(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=600) as response:
            sys.stdout.buffer.write(response.read())
        return
    agent = Agent()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                result = dict(result=agent.rpc(**request))
            except Exception as error:
                result = dict(error=str(error))
            body = json.dumps(result).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(('127.0.0.1', 9091), Handler).serve_forever()


if __name__ == '__main__':
    main()
