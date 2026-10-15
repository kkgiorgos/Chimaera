"""Durable local supervisor. Workers are detached sibling Docker containers."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import uuid

from common import Store, atomic_json, identifier, run, case_log

ROOT = Path(os.environ.get('CHIMAERA_REPO', '/repo'))
DATA = ROOT / '.chimaera-orchestrator'
IMAGE = 'chimaera-supervisor:local'


class Agent:
    def __init__(self, root=ROOT, data=DATA):
        self.root, self.data = Path(root), Path(data)
        self.store = Store(self.data / 'agent.sqlite')
        self.lock = threading.RLock()

    def rpc(self, method, **params):
        if method not in {'snapshot', 'prepare', 'start', 'stop', 'logs', 'cleanup', 'asset_destination', 'reset_prepare'}:
            raise ValueError('Unknown supervisor operation')
        with self.lock:
            return getattr(self, method)(**params)

    def directory(self, experiment):
        return self.data / 'experiments' / identifier(experiment)

    def prepare(self, experiment, config, cpus, memory, build_jobs, source_identity, disk='', kernel='', calibration=None, generation=0):
        self.check_generation(experiment, generation)
        directory = self.directory(experiment)
        key = experiment + '-prepare'
        existing = self.store.get('tasks', key)
        if existing:
            return existing
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / 'request.json', dict(experiment=experiment, config=config, cpus=cpus,
            memory=memory, build_jobs=build_jobs, disk=disk, kernel=kernel, calibration=calibration, source_identity=source_identity))
        command = ['docker', 'run', '-d', '--name', 'chimaera-' + key,
            '--label', 'chimaera.experiment=' + experiment,
            '--user', f'{os.getuid()}:{os.getgid()}', '--group-add', str(Path('/var/run/docker.sock').stat().st_gid),
            '-v', '/var/run/docker.sock:/var/run/docker.sock', '-v', f'{self.root}:{self.root}',
            '-e', 'CHIMAERA_REPO=' + str(self.root), '-e', 'HOME=/tmp',
            '-e', 'CHIMAERA_OPERATION=' + experiment, IMAGE,
            'python3', '/app/pipeline.py', str(directory)]
        return self.launch(key, experiment, 'prepare', command, directory)

    def asset_destination(self, experiment):
        path = self.directory(experiment) / 'shared-assets'
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

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
            for name in ('inputs', 'ready.json', 'stage.json', 'export-assets', 'experiment.json',
                         'request.json', 'worker.log', 'preparation-report'):
                path = directory / name
                if path.exists():
                    path.rename(archive / name)
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
        self.check_generation(experiment, generation)
        identifier(case)
        key = identifier(attempt)
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
        command.remove('--rm')
        command[2:2] = ['-d', '--label', 'chimaera.experiment=' + experiment]
        return self.launch(key, experiment, 'case', command, output, case=case)

    def reconcile(self):
        for task in self.store.all('tasks'):
            if task['status'] == 'accepted':
                self.ensure_started(task)
            if task['status'] != 'running':
                continue
            try:
                state = json.loads(run(['docker', 'inspect', task['container']], text=True, timeout=10))[0]['State']
            except RuntimeError as error:
                task.update(status='failed', error='Container missing: ' + str(error), finished=time.time())
                self.store.put('tasks', task['id'], task)
                continue
            if state['Running']:
                continue
            output = Path(task['output'])
            log = output / 'worker.log'
            task.update(status='completed' if state['ExitCode'] == 0 else 'failed',
                        exit_code=state['ExitCode'], oom=state.get('OOMKilled', False), finished=time.time())
            try:
                output.mkdir(parents=True, exist_ok=True)
                with log.open('wb') as stream:
                    subprocess.run(['docker', 'logs', task['container']], stdout=stream,
                                   stderr=subprocess.STDOUT, timeout=30, check=True)
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
                if task['status'] == 'completed':
                    try:
                        ready = json.loads((output / 'ready.json').read_text())
                        if not ready.get('records') or not ready.get('provenance'):
                            raise ValueError('Missing calibration or provenance')
                    except (OSError, ValueError, AttributeError) as error:
                        task.update(status='failed', error='Preparation exited without valid ready.json: ' + str(error))
                self.preparation_report(task)
            self.store.put('tasks', task['id'], task)

    def preparation_report(self, task):
        output = Path(task['output'])
        try:
            report = output / 'preparation-report'
            report.mkdir(parents=True, exist_ok=True)
            for name in ('worker.log', 'request.json', 'ready.json', 'stage.json', 'experiment.json',
                         'inputs/inputs.json', 'inputs/runner.json', 'inputs/plan.json'):
                source = output / name
                if source.is_file():
                    destination = report / name
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
                report = Path(task['output']) / 'preparation-report'
                if task['status'] in ('failed', 'stopped') and not report.exists():
                    self.preparation_report(task)
        containers = [t['container'] for t in tasks if t['status'] == 'running']
        try:
            stats = run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *containers], text=True, timeout=15) if containers else ''
        except (RuntimeError, OSError, subprocess.SubprocessError):
            stats = ''
        return dict(tasks=tasks, stats=[json.loads(line) for line in stats.splitlines()],
            host=dict(cpus=os.cpu_count(), load=os.getloadavg(), memory=Path('/proc/meminfo').read_text()), time=time.time())

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
                task.update(status='stopped', finished=time.time())
                try:
                    with (Path(task['output']) / 'worker.log').open('wb') as stream:
                        subprocess.run(['docker', 'logs', task['container']], stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=30)
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
        return True


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('mode', choices=['serve', 'rpc'])
    args = cli.parse_args()
    if args.mode == 'rpc':
        request = urllib.request.Request('http://127.0.0.1:9091', data=sys.stdin.buffer.read(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=35) as response:
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
