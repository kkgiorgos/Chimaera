"""Persistent single-experiment scheduler; all remote work is reconciled by ID."""
from concurrent.futures import ThreadPoolExecutor
import copy
import heapq
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import sys
import tarfile
import tempfile
import threading
import time
import uuid

from common import Store, atomic_json, digest, event, identifier, source_identity
from transport import SSH

BUSY = {'dispatching', 'running', 'collecting'}
OCCUPIED = {'dispatching', 'running'}
ACTIVE = {'preparing', 'running', 'stopping', 'needs_attention'}


def validate_config(root, config):
    if not isinstance(config, dict):
        raise ValueError('Experiment must be a JSON object')
    config = copy.deepcopy(config)
    path = (root / config.get('ramulator_config', '')).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('ramulator_config must name a file inside the repository')
    config['ramulator_config'] = str(path.relative_to(root.resolve()))
    resolved = dict(config, ramulator_config=str(path))
    sys.path.insert(0, str(root / 'ros/ball_catching_ws/scripts'))
    from run_memory_suite import load_config, plan
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'experiment.json'
        atomic_json(source, resolved)
        data = load_config(source)
    return config, plan(data)


def validate_machine(machine):
    allowed = {'id', 'name', 'target', 'port', 'repo', 'workers', 'cpus', 'memory', 'build_jobs', 'disk', 'kernel'}
    machine = {key: value for key, value in machine.items() if key in allowed}
    identifier(machine['id'])
    if not isinstance(machine.get('target'), str) or not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]+', machine['target']) or machine['target'].startswith('-'):
        raise ValueError('SSH target must be a hostname or user@hostname')
    for key in ('repo', 'disk', 'kernel'):
        value = machine.get(key, '')
        if key == 'repo' or value:
            if not isinstance(value, str) or not value.startswith('/') or any(c in value for c in '\n\r\0,:'):
                raise ValueError(f'{key} must be an absolute path without commas, colons or newlines')
        machine[key] = value
    for key, default, maximum in [('port', 22, 65535), ('workers', 1, 1024), ('build_jobs', 2, 1024)]:
        value = machine.setdefault(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{key} must be between 1 and {maximum}')
    cpus = machine.setdefault('cpus', 1)
    if type(cpus) not in (int, float) or not math.isfinite(cpus) or cpus <= 0:
        raise ValueError('Worker CPUs must be positive')
    if not re.fullmatch(r'[1-9][0-9]*[mMgG]', machine.setdefault('memory', '2g')):
        raise ValueError('Worker memory must be a size such as 2g or 2048m')
    if not isinstance(machine.get('name'), str) or not machine['name'].strip():
        raise ValueError('Machine name is required')
    return machine


def safe_extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe result archive member')
        stream.extractall(destination, members=members)


def estimates(experiment, machines, now=None):
    """Conservative empirical ETAs; readiness has no meaningful linear percentage."""
    now = now or time.time()
    samples = {}
    for case in experiment['cases']:
        for attempt in case['attempts']:
            if attempt.get('status') == 'completed' and attempt.get('finished'):
                key = (attempt['machine'], attempt.get('cpus'), attempt.get('memory'))
                samples.setdefault(key, []).append(attempt['finished'] - attempt['started'])
    durations = {key: statistics.median(values) for key, values in samples.items()}
    active_remaining = []
    for case in experiment['cases']:
        case['eta_seconds'] = None
        case['eta_basis'] = 'Insufficient data'
        if case['status'] not in BUSY:
            continue
        attempt = case['attempts'][-1]
        machine = next((m for m in machines if m['id'] == attempt['machine']), {})
        if not machine.get('online') or (machine.get('seen') and now - machine['seen'] > 30):
            case['eta_basis'] = 'Machine progress is stale'
            active_remaining.append(None)
            continue
        progress = attempt.get('progress', {})
        speed = progress.get('average_speed')
        remaining = None
        if speed and speed > 0 and progress.get('throw_seconds') is not None:
            remaining = max(0, progress['duration_seconds'] - progress['throw_seconds']) / speed
            case['eta_basis'] = 'Throw progress; excludes finalization'
        else:
            key = (attempt['machine'], attempt.get('cpus'), attempt.get('memory'))
            duration = durations.get(key)
            if duration and now - attempt['started'] < duration:
                remaining = duration - (now - attempt['started'])
                case['eta_basis'] = 'Previous cases on this machine'
        case['eta_seconds'] = remaining
        active_remaining.append(remaining)
    slots = []
    for machine in machines:
        if machine['id'] not in experiment['machines']:
            continue
        key = (machine['id'], machine['cpus'], machine['memory'])
        duration = durations.get(key)
        if duration is None or not machine.get('online'):
            continue
        busy = [c for c in experiment['cases'] if c['status'] in BUSY and c['attempts'][-1]['machine'] == machine['id']]
        for index in range(machine['workers']):
            available = busy[index]['eta_seconds'] if index < len(busy) else 0
            if available is not None:
                heapq.heappush(slots, (available, duration, machine['id'], index))
    queued = sum(c['status'] == 'queued' for c in experiment['cases'])
    experiment['eta_seconds'] = None
    experiment['eta_basis'] = 'Insufficient data'
    if experiment['status'] == 'running' and slots and all(x is not None for x in active_remaining):
        for _ in range(queued):
            available, duration, machine, index = heapq.heappop(slots)
            heapq.heappush(slots, (available + duration, duration, machine, index))
        experiment['eta_seconds'] = max([x[0] for x in slots] + active_remaining + [0])
        experiment['eta_basis'] = 'Estimate from observed case durations; excludes collection/reporting'
    return experiment


class Controller:
    def __init__(self, root, data, transport=None):
        self.root, self.data = Path(root).resolve(), Path(data)
        self.store = Store(self.data / 'controller.sqlite')
        self.remote = transport or SSH()
        self.pool = ThreadPoolExecutor(max_workers=12)
        self.io_pool = ThreadPoolExecutor(max_workers=3)
        self.stop_pool = ThreadPoolExecutor(max_workers=3)
        self.jobs = {}
        self.last_poll = {}
        self.collection_cancellations = {}
        self.sdk_cancellations = {}
        self.collection_lock = threading.RLock()
        if not self.store.get('settings', 'main'):
            self.store.put('settings', 'main', dict(progress_enabled=True, progress_interval=3600, last_progress=0))
        # Older databases coupled failed execution to its unfinished log transfer.
        for experiment in self.store.all('experiments'):
            for case in experiment['cases']:
                if case['status'] == 'collecting' and case['attempts'][-1]['status'] in {'failed', 'stopped'}:
                    case['status'] = 'failed'
            self.save(experiment)

    def submit(self, key, kind, context, operation):
        if key not in self.jobs:
            pool = self.io_pool if kind in {'collect', 'prepare_collect', 'transfer', 'sdk_transfer'} else self.stop_pool if kind == 'stop' else self.pool
            self.jobs[key] = (kind, context, pool.submit(operation))

    def close(self):
        for cancellation in self.sdk_cancellations.values():
            cancellation.set()
        for pool in (self.pool, self.io_pool, self.stop_pool):
            pool.shutdown(wait=True)

    def command(self, kind, body):
        """HTTP validates and appends commands; only scheduler mutates scheduling state."""
        body = copy.deepcopy(body)
        if kind == 'machine':
            if not body.get('id'):
                body['id'] = uuid.uuid4().hex
            body = validate_machine(body)
        elif kind == 'experiment':
            body['config'], body['points'] = validate_config(self.root, body['config'])
            if not isinstance(body.get('name'), str) or not body['name'].strip():
                raise ValueError('Experiment name is required')
            machines = {m['id'] for m in self.store.all('machines')}
            if not body.get('machines') or not set(body['machines']).issubset(machines):
                raise ValueError('Choose at least one configured machine')
            body.setdefault('calibration_machine', body['machines'][0])
            if body['calibration_machine'] not in body['machines']:
                raise ValueError('Calibration machine must be one of the selected machines')
        elif kind == 'settings':
            if type(body.get('progress_enabled')) is not bool or type(body.get('progress_interval')) is not int or body['progress_interval'] < 60:
                raise ValueError('Progress interval must be at least 60 seconds')
        elif kind not in {'deploy', 'clean_assets', 'stop', 'resume', 'retry', 'accept_partial', 'delete', 'cleanup', 'retry_prepare', 'retry_collection'}:
            raise ValueError('Unknown action')
        key = uuid.uuid4().hex
        self.store.put('commands', key, dict(id=key, kind=kind, body=body, status='pending'))
        return key

    def apply(self, command):
        kind, body = command['kind'], command['body']
        if kind == 'machine':
            previous = self.store.get('machines', body['id'], {})
            if previous.get('cleaning') and any(previous[key] != body[key] for key in ('target', 'port', 'repo', 'disk', 'kernel')):
                raise ValueError('Wait for asset cleanup before changing machine connection or asset paths')
            if previous and any(e['status'] in ACTIVE and body['id'] in e['machines'] for e in self.store.all('experiments')):
                # Resource edits affect future dispatch; connection/path edits must wait.
                if any(previous[key] != body[key] for key in ('target', 'port', 'repo', 'disk', 'kernel')):
                    raise ValueError('Stop the active experiment before changing machine connection or asset paths')
            self.store.put('machines', body['id'], dict(previous, **body))
        elif kind == 'settings':
            self.store.put('settings', 'main', dict(self.store.get('settings', 'main'), **body))
        elif kind == 'deploy':
            machine = self.machine(body['id'])
            machine['deploying'] = True
            self.store.put('machines', machine['id'], machine)
            self.submit('deploy-' + machine['id'], 'deploy', machine['id'], lambda: self.remote.deploy(machine))
            event(self.store, f"Deploying supervisor on {machine['name']}")
        elif kind == 'clean_assets':
            machine = self.machine(body['id'])
            if any(e['status'] in ACTIVE and machine['id'] in e['machines'] for e in self.store.all('experiments')):
                raise ValueError('Stop the experiment before cleaning unused machine assets')
            machine.update(cleaning=True, cleanup_error=None)
            self.store.put('machines', machine['id'], machine)
        elif kind == 'experiment':
            key = command['id']
            if self.store.get('experiments', key):
                return
            experiment = dict(id=key, name=body['name'], config=body['config'], machines=body['machines'],
                created=time.time(), status='queued', preparations={}, calibration=None, generation=0,
                calibration_machine=body.get('calibration_machine', body['machines'][0]),
                source_identity=source_identity(self.root),
                cases=[dict(id=p['id'], point=p, status='queued', attempts=[]) for p in body['points']])
            self.store.put('experiments', key, experiment)
            atomic_json(self.data / 'results' / key / 'experiment.json', body['config'])
            event(self.store, f"Queued {experiment['name']} ({len(experiment['cases'])} cases)")
        else:
            experiment = self.experiment(body['id'])
            if kind == 'stop':
                self.sdk_cancellations.setdefault(experiment['id'], threading.Event()).set()
                for preparation in experiment['preparations'].values():
                    if preparation['status'] == 'copying_sdk':
                        preparation['status'] = 'stopped'
                if experiment['status'] == 'queued':
                    experiment['status'] = 'stopped'
                elif experiment['status'] in ACTIVE:
                    experiment['status'] = 'stopping'
                    experiment['stop_acks'] = []
                else:
                    raise ValueError('Experiment is not running')
            elif kind == 'resume':
                if experiment['status'] != 'stopped':
                    raise ValueError('Only stopped experiments can resume')
                if experiment.get('remote_cleaned') or experiment.get('results_deleted'):
                    raise ValueError('Results or inputs were deleted; clone this configuration for a new run')
                for case in experiment['cases']:
                    if case['status'] == 'collecting':
                        case['resume_after_collection'] = True
                    elif case['status'] != 'completed':
                        case['status'] = 'queued'
                experiment['status'] = 'queued'
                self.sdk_cancellations[experiment['id']] = threading.Event()
                experiment['generation'] = experiment.get('generation', 0) + 1
                for machine_id, preparation in experiment['preparations'].items():
                    if preparation['status'] in ('failed', 'stopped'):
                        if preparation.get('started') is not None:
                            experiment.setdefault('preparation_ignored', {})[machine_id] = preparation['started']
                        preparation['status'] = 'resetting'
            elif kind == 'retry':
                if experiment['status'] not in {'running', 'needs_attention'}:
                    raise ValueError('Resume the experiment before retrying cases')
                for case in experiment['cases']:
                    if case['status'] == 'failed' and (not body.get('case') or case['id'] == body['case']):
                        case['status'] = 'queued'
                experiment['status'] = 'running'
            elif kind == 'retry_collection':
                if experiment.get('results_deleted'):
                    raise ValueError('Collected results have been deleted')
                for case in experiment['cases']:
                    for attempt in case['attempts']:
                        if attempt.get('collection_status') == 'failed':
                            attempt['collection_status'] = 'pending'
                            attempt.pop('collection_error', None)
                for preparation in experiment['preparations'].values():
                    if preparation.get('collection_status') == 'failed':
                        preparation['collection_status'] = 'pending'
                        preparation.pop('collection_error', None)
            elif kind == 'retry_prepare':
                machine = self.machine(body['machine'])
                preparation = experiment['preparations'].get(machine['id'])
                if not preparation or preparation['status'] not in ('failed', 'stopped'):
                    raise ValueError('Only failed or stopped preparation can be retried')
                if preparation.get('started') is not None:
                    experiment.setdefault('preparation_ignored', {})[machine['id']] = preparation['started']
                preparation['status'] = 'resetting'
                self.submit('reset-' + machine['id'], 'reset', (experiment['id'], machine['id']),
                    lambda: self.remote.call(machine, 'reset_prepare', experiment=experiment['id']))
            elif kind == 'accept_partial':
                if experiment['status'] != 'needs_attention' or any(c['status'] in BUSY for c in experiment['cases']):
                    raise ValueError('Wait for active cases to finish before accepting partial results')
                experiment.update(status='partial', finished=time.time())
                event(self.store, f"{experiment['name']}: partial results accepted", notify=True)
            elif kind in {'delete', 'cleanup'}:
                if kind == 'delete':
                    executing = any(c['status'] in OCCUPIED for c in experiment['cases'])
                    preparing = experiment['status'] != 'stopped' and any(p['status'] in {'preparing', 'running', 'accepted', 'copying_sdk'}
                                    for p in experiment['preparations'].values())
                    if executing or preparing or experiment['status'] == 'stopping':
                        raise ValueError('Stop active workers before deleting the experiment')
                    with self.collection_lock:
                        self.cancel_collection(experiment['id']).set()
                        experiment.update(results_deleted=True, status='deleted')
                        for case in experiment['cases']:
                            for attempt in case['attempts']:
                                if not attempt.get('collected'):
                                    attempt['collection_status'] = 'cancelled'
                        self.save(experiment)
                        shutil.rmtree(self.data / 'results' / experiment['id'], ignore_errors=True)
                        shutil.rmtree(self.data / 'assets' / experiment['id'], ignore_errors=True)
                        shutil.rmtree(self.data / 'collections' / experiment['id'], ignore_errors=True)
                else:
                    if experiment['status'] in ACTIVE or experiment['status'] == 'queued':
                        raise ValueError('Stop the experiment before cleaning remote results')
                    # No remote deletion until every accepted attempt has a verified local copy.
                    attempts = [a for c in experiment['cases'] for a in c['attempts']]
                    preparations = experiment['preparations'].values()
                    if not body.get('discard_uncollected') and (experiment.get('results_deleted') or any(not a.get('collected') for a in attempts) or any(not p.get('collected') for p in preparations)):
                        raise ValueError('Collect every attempt before remote cleanup')
                    for machine_id in experiment['preparations']:
                        machine = self.machine(machine_id)
                        self.submit('clean-' + machine_id, 'cleanup', (experiment['id'], machine_id),
                            lambda m=machine: self.remote.call(m, 'cleanup', experiment=experiment['id']))
            self.save(experiment)

    def machine(self, key):
        value = self.store.get('machines', identifier(key))
        if not value:
            raise ValueError('Unknown machine')
        return value

    def experiment(self, key):
        value = self.store.get('experiments', identifier(key))
        if not value:
            raise ValueError('Unknown experiment')
        return value

    def save(self, experiment):
        self.store.put('experiments', experiment['id'], experiment)

    def cancel_collection(self, experiment_id):
        return self.collection_cancellations.setdefault(experiment_id, threading.Event())

    def tick(self):
        for command in self.store.all('commands'):
            if command['status'] != 'pending':
                continue
            try:
                self.apply(command)
                command['status'] = 'done'
            except Exception as error:
                command.update(status='failed', error=str(error))
                event(self.store, str(error), 'error')
            self.store.put('commands', command['id'], command)
        for key, (kind, context, future) in list(self.jobs.items()):
            if not future.done():
                continue
            del self.jobs[key]
            try:
                result = future.result()
                self.finished(kind, context, result)
            except Exception as error:
                self.failed(kind, context, str(error))
        for machine in self.store.all('machines'):
            if machine.get('cleaning'):
                self.submit('clean-assets-' + machine['id'], 'clean_assets', machine['id'],
                    lambda m=machine: self.remote.call(m, 'clean_assets', keep_paths=[m.get('disk'), m.get('kernel')]))
            if machine.get('deploying'):
                self.submit('deploy-' + machine['id'], 'deploy', machine['id'], lambda m=machine: self.remote.deploy(m))
            if machine.get('deployed') and time.time() - self.last_poll.get(machine['id'], 0) >= 5:
                self.last_poll[machine['id']] = time.time()
                self.submit('poll-' + machine['id'], 'poll', machine['id'], lambda m=machine: self.remote.call(m, 'snapshot'))
        experiments = self.store.all('experiments')
        active = next((e for e in experiments if e['status'] in ACTIVE), None)
        if not active:
            active = next((e for e in experiments if e['status'] == 'queued'), None)
            if active:
                active['status'] = 'preparing'
                active['activated'] = time.time()
                self.save(active)
        if active:
            self.schedule(active)

    def finished(self, kind, context, result):
        if kind == 'clean_assets':
            machine = self.machine(context)
            machine.update(cleaning=False, cleanup_result=result, cleanup_error=None)
            self.store.put('machines', context, machine)
            event(self.store, f"{machine['name']}: removed {result['images']} unused images and {result['disks']} guest disks")
        elif kind == 'deploy':
            machine = self.machine(context)
            machine.update(deployed=True, deploying=False, error=None)
            self.store.put('machines', context, machine)
            event(self.store, f"Supervisor deployed on {machine['name']}")
        elif kind == 'poll':
            machine = self.machine(context)
            recovered = not machine.get('online')
            machine.update(online=True, seen=time.time(), snapshot=result, error=None)
            self.store.put('machines', context, machine)
            if recovered:
                event(self.store, f"{machine['name']} connected")
            self.ingest(machine, result)
        elif kind in {'reset', 'cleanup'}:
            experiment_id, machine_id = context
            experiment = self.experiment(experiment_id)
            if kind == 'reset' and experiment.get('results_deleted'):
                return
            if kind == 'reset':
                experiment['preparations'].pop(machine_id, None)
            else:
                experiment['preparations'][machine_id] = dict(status='pending' if kind != 'cleanup' else 'cleaned', collected=kind == 'cleanup')
                if kind == 'cleanup' and all(p['status'] == 'cleaned' for p in experiment['preparations'].values()):
                    experiment['remote_cleaned'] = True
            self.save(experiment)
        elif kind == 'sdk_transfer':
            experiment = self.experiment(context[0])
            if context[2] != experiment.get('generation', 0):
                return
            preparation = experiment['preparations'].get(context[1], {})
            if preparation.get('status') == 'copying_sdk' and experiment['status'] not in {'stopped', 'stopping', 'deleted'}:
                preparation.update(status='pending', sdk_image_id=result, phase='Images copied')
                experiment['calibration']['provenance']['sdk_image_id'] = result
                self.save(experiment)
        elif kind == 'stop':
            if isinstance(result, dict) and 'tasks' in result:
                self.ingest(self.machine(context[1]), result)
            experiment = self.experiment(context[0])
            acks = experiment.setdefault('stop_acks', [])
            if context[1] not in acks:
                acks.append(context[1])
            self.save(experiment)
        elif kind == 'collect':
            experiment_id, case_id, attempt_id = context
            experiment = self.experiment(experiment_id)
            if experiment.get('results_deleted'):
                shutil.rmtree(self.data / 'collections' / experiment_id, ignore_errors=True)
                return
            case = next(c for c in experiment['cases'] if c['id'] == case_id)
            attempt = next(a for a in case['attempts'] if a['id'] == attempt_id)
            attempt['collected'] = True
            attempt['collection_status'] = 'completed'
            attempt.pop('collection_error', None)
            if case['attempts'][-1]['id'] == attempt_id and case['status'] != 'queued':
                case['status'] = 'completed' if attempt['status'] == 'completed' else 'failed'
                if case.pop('resume_after_collection', False) and case['status'] != 'completed':
                    case['status'] = 'queued'
            self.save(experiment)
            event(self.store, f"{experiment['name']} / {case_id}: {case['status']}",
                  'error' if case['status'] == 'failed' else 'info', notify=True)
        elif kind == 'prepare_collect':
            experiment = self.experiment(context[0])
            if experiment.get('results_deleted'):
                shutil.rmtree(self.data / 'collections' / context[0], ignore_errors=True)
                return
            experiment['preparations'][context[1]]['collected'] = True
            experiment['preparations'][context[1]]['collection_status'] = 'completed'
            experiment['preparations'][context[1]].pop('collection_error', None)
            self.save(experiment)
        # start/prepare/stop are reconciled from the next authoritative snapshot.

    def failed(self, kind, context, error):
        if kind == 'clean_assets':
            machine = self.machine(context)
            machine.update(cleaning=False, cleanup_error=error)
            self.store.put('machines', context, machine)
            event(self.store, f"{machine['name']}: asset cleanup failed: {error}", 'error')
        elif kind in {'poll', 'deploy'}:
            machine = self.machine(context)
            was_online = machine.get('online')
            machine.update(online=False, error=error)
            if kind == 'deploy':
                machine['deploying'] = False
            self.store.put('machines', context, machine)
            if was_online or kind == 'deploy':
                event(self.store, f"{machine['name']}: {error}", 'error', notify=True)
        else:
            if kind == 'sdk_transfer':
                experiment = self.experiment(context[0])
                if context[2] != experiment.get('generation', 0) or experiment['status'] in {'stopping', 'stopped', 'deleted'}:
                    return
                preparation = experiment['preparations'].get(context[1], {})
                if preparation.get('status') == 'copying_sdk':
                    preparation.update(status='failed', error=error, phase='Image transfer failed')
                    self.save(experiment)
            if kind in {'collect', 'prepare_collect'}:
                experiment = self.experiment(context[0])
                if experiment.get('results_deleted'):
                    shutil.rmtree(self.data / 'collections' / context[0], ignore_errors=True)
                    return
                if kind == 'collect':
                    case = next(c for c in experiment['cases'] if c['id'] == context[1])
                    attempt = next(a for a in case['attempts'] if a['id'] == context[2])
                    attempt.update(collection_status='failed', collection_error=error)
                    if case['attempts'][-1]['id'] == attempt['id'] and case['status'] not in {'queued', 'running', 'dispatching'}:
                        case['status'] = 'collection_failed' if attempt['status'] == 'completed' else 'failed'
                        if case.pop('resume_after_collection', False) and attempt['status'] != 'completed':
                            case['status'] = 'queued'
                else:
                    experiment['preparations'][context[1]].update(collection_status='failed', collection_error=error)
                self.save(experiment)
            marker = f'{kind}-{context}-{error}'
            if not self.store.get('errors', marker):
                event(self.store, f'{kind}: {error}', 'error', notify=True)
                self.store.put('errors', marker, True)

    def ingest(self, machine, snapshot):
        for experiment in self.store.all('experiments'):
            if machine['id'] not in experiment['machines'] or experiment.get('results_deleted'):
                continue
            for task in snapshot['tasks']:
                if task['experiment'] != experiment['id']:
                    continue
                if task['kind'] == 'prepare':
                    if task.get('started') is not None and experiment.get('preparation_ignored', {}).get(machine['id']) == task['started']:
                        continue
                    prior = experiment['preparations'].get(machine['id'], {})
                    experiment['preparations'][machine['id']] = dict(prior, status=task['status'], collected=prior.get('collected', False),
                        task=task['id'], started=task.get('started'), phase=task.get('phase'), error=task.get('error'))
                    if task['status'] == 'completed' and task.get('ready'):
                        experiment['preparations'][machine['id']]['status'] = 'ready'
                        if not experiment['calibration']:
                            experiment['calibration'] = task['ready']
                            experiment['calibration_machine'] = machine['id']
                            atomic_json(self.data / 'results' / experiment['id'] / 'calibration.json', task['ready'])
                    if task['status'] in ('completed', 'failed', 'stopped') and not prior.get('collected') and prior.get('collection_status') != 'failed':
                        self.submit('prepare-collect-' + machine['id'], 'prepare_collect', (experiment['id'], machine['id']),
                            lambda m=machine, e=experiment['id'], p=task['output']: self.collect_preparation(m, e, p))
                    if task['status'] == 'failed' and not experiment['preparations'][machine['id']].get('notified'):
                        # A persistent marker is kept separately because snapshots are repeatedly ingested.
                        marker = experiment['id'] + '-' + task['id'] + '-' + str(task.get('started')) + '-failure'
                        if not self.store.get('markers', marker):
                            event(self.store, f"{experiment['name']}: preparation failed on {machine['name']}", 'error', notify=True)
                            self.store.put('markers', marker, True)
                else:
                    for case in experiment['cases']:
                        attempt = next((a for a in case['attempts'] if a['id'] == task['id']), None)
                        if not attempt:
                            continue
                        attempt.update(progress=task.get('progress', {}), remote_output=task['output'])
                        if task['status'] in {'completed', 'failed', 'stopped'}:
                            attempt.update(status=task['status'], finished=task.get('finished', time.time()),
                                exit_code=task.get('exit_code'), oom=task.get('oom', False))
                            if task['status'] == 'failed' and not attempt.get('failure_notified'):
                                attempt['failure_notified'] = True
                                event(self.store, f"{experiment['name']} / {case['id']}: failed on {machine['name']}" +
                                    (' (memory limit exceeded)' if task.get('oom') else ''), 'error', notify=True)
                            if not attempt.get('collected') and attempt.get('collection_status') != 'failed':
                                if case['attempts'][-1]['id'] == attempt['id'] and case['status'] != 'queued':
                                    case['status'] = 'collecting' if task['status'] == 'completed' else 'failed'
                                self.submit('collect-' + attempt['id'], 'collect', (experiment['id'], case['id'], attempt['id']),
                                    lambda m=machine, e=copy.deepcopy(experiment), c=case['id'], a=copy.deepcopy(attempt): self.collect(m, e, c, a))
                        elif case['attempts'][-1]['id'] == attempt['id']:
                            case['status'] = 'running'
                            attempt['status'] = 'running'
                            if not attempt.get('start_notified'):
                                attempt['start_notified'] = True
                                event(self.store, f"{experiment['name']} / {case['id']}: started on {machine['name']}", notify=True)
            self.save(experiment)

    def collect(self, machine, experiment, case_id, attempt):
        directory = self.data / 'results' / experiment['id']
        cancelled = self.cancel_collection(experiment['id'])
        with self.collection_lock:
            if cancelled.is_set():
                return False
            stage = self.data / 'collections' / experiment['id'] / attempt['id']
            stage.mkdir(parents=True, exist_ok=True)
        archive = stage / 'result.tar.gz.part'
        self.remote.fetch(machine, attempt['remote_output'], archive, cancel=cancelled)
        destination = directory / 'attempts' / attempt['id']
        incoming = stage / 'incoming'
        if incoming.exists():
            shutil.rmtree(incoming)
        safe_extract(archive, incoming)
        if attempt['status'] == 'completed':
            manifest = json.loads((incoming / 'suite/experiment_manifest.json').read_text())
            point = next(p for p in manifest['points'] if p['id'] == case_id)
            if point['status'] != 'complete' or not point['trials']:
                raise ValueError('Container exited successfully without a completed benchmark result')
        atomic_json(incoming / 'collection.json', dict(machine=machine['id'], attempt=attempt,
            archive_sha256=digest(archive), collected=time.time()))
        with self.collection_lock:
            if cancelled.is_set():
                shutil.rmtree(incoming, ignore_errors=True)
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            incoming.rename(destination)
        archive.unlink()
        return True

    def collect_preparation(self, machine, experiment_id, output):
        base = self.data / 'results' / experiment_id / 'preparations'
        cancelled = self.cancel_collection(experiment_id)
        with self.collection_lock:
            if cancelled.is_set():
                return False
            stage = self.data / 'collections' / experiment_id / ('preparation-' + machine['id'])
            stage.mkdir(parents=True, exist_ok=True)
        archive = stage / 'result.tar.gz.part'
        self.remote.fetch(machine, output + '/preparation-report', archive, cancel=cancelled)
        incoming = stage / 'incoming'
        if incoming.exists():
            shutil.rmtree(incoming)
        safe_extract(archive, incoming)
        destination = base / machine['id']
        with self.collection_lock:
            if cancelled.is_set():
                shutil.rmtree(incoming, ignore_errors=True)
                return False
            base.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            incoming.rename(destination)
        archive.unlink()
        return True

    def copy_sdk(self, experiment, destination, cancelled):
        source = self.machine(experiment.get('calibration_machine', experiment['machines'][0]))
        provenance = experiment['calibration']['provenance']
        image = provenance.get('sdk_image_id')
        if not image:
            key = provenance.get('build_inputs', {}).get('sdk')
            if not key:
                raise ValueError('Calibration SDK identity is missing; prepare the calibration machine again')
            image = self.remote.image_id(source, 'chimaera:sdk-' + key[:16])
        # Track imports before streaming so interrupted transfers can be cleaned too.
        self.remote.call(destination, 'remember_image', image=image)
        sdk_id = self.remote.copy_image(source, destination, image, cancel=cancelled)
        application = provenance.get('application_image_id')
        if application:
            self.remote.call(destination, 'remember_image', image=application)
            self.remote.copy_image(source, destination, application, cancel=cancelled)
        return sdk_id

    def schedule(self, experiment):
        machines = [self.machine(key) for key in experiment['machines']]
        if experiment['status'] == 'stopping':
            pending = []
            for machine in machines:
                tasks = [t for t in machine.get('snapshot', {}).get('tasks', []) if t['experiment'] == experiment['id']]
                involved = machine['id'] in experiment['preparations'] or any(a['machine'] == machine['id'] for c in experiment['cases'] for a in c['attempts'])
                if involved and machine['id'] not in experiment.get('stop_acks', []):
                    pending.append(machine['id'])
                    self.submit('stop-' + machine['id'], 'stop', (experiment['id'], machine['id']),
                        lambda m=machine: self.remote.call(m, 'stop', experiment=experiment['id'], generation=experiment.get('generation', 0)))
            experiment['stop_pending'] = pending
            if not pending:
                experiment['status'] = 'stopped'
                for case in experiment['cases']:
                    if case['status'] in OCCUPIED:
                        case['status'] = 'stopped'
                event(self.store, f"{experiment['name']}: stopped", notify=True)
            self.save(experiment)
            return
        producer = experiment.get('calibration_machine', machines[0]['id'])
        ready = []
        for machine in machines:
            if machine.get('cleaning'):
                continue
            preparation = experiment['preparations'].get(machine['id'])
            if preparation and preparation['status'] == 'resetting':
                self.submit('reset-' + machine['id'], 'reset', (experiment['id'], machine['id']),
                    lambda m=machine: self.remote.call(m, 'reset_prepare', experiment=experiment['id']))
                continue
            if preparation and preparation['status'] == 'ready' and machine.get('online'):
                ready.append(machine)
                continue
            if preparation and preparation['status'] == 'transferring':
                # Upgrade old persisted runs: assets are now built locally.
                preparation = None
            if preparation and preparation['status'] not in ('pending', 'preparing', 'copying_sdk'):
                continue
            if not machine.get('online'):
                continue
            if not experiment['calibration'] and machine['id'] != producer:
                continue
            if experiment['calibration'] and machine['id'] != producer and not (preparation or {}).get('sdk_image_id'):
                experiment['preparations'][machine['id']] = dict(status='copying_sdk', phase='Copying prepared images')
                cancelled = self.sdk_cancellations.setdefault(experiment['id'], threading.Event())
                self.submit('sdk-' + machine['id'], 'sdk_transfer', (experiment['id'], machine['id'], experiment.get('generation', 0)),
                    lambda m=machine, e=copy.deepcopy(experiment), c=cancelled: self.copy_sdk(e, m, c))
                continue
            experiment['preparations'][machine['id']] = dict(preparation or {}, status='preparing')
            self.submit('prepare-' + machine['id'], 'prepare', (experiment['id'], machine['id']),
                lambda m=machine, e=copy.deepcopy(experiment): self.remote.call(m, 'prepare', experiment=e['id'],
                    config=e['config'], cpus=m['cpus'], memory=m['memory'], build_jobs=m['build_jobs'],
                    source_identity=e['source_identity'],
                    generation=e.get('generation', 0),
                    disk=m['disk'], kernel=m['kernel'],
                    calibration=e['calibration'] if m['id'] != e.get('calibration_machine', machines[0]['id']) else None))
        if ready:
            experiment['status'] = 'running'
        for machine in ready:
            busy = sum(c['status'] in OCCUPIED and c['attempts'][-1]['machine'] == machine['id'] for c in experiment['cases'])
            for case in [c for c in experiment['cases'] if c['status'] == 'queued'][:max(0, machine['workers'] - busy)]:
                attempt = dict(id=uuid.uuid4().hex, machine=machine['id'], started=time.time(), status='dispatching',
                               cpus=machine['cpus'], memory=machine['memory'])
                case['attempts'].append(attempt)
                case['status'] = 'dispatching'
                # Persist the reservation before issuing SSH; retries always use the same attempt ID.
                self.save(experiment)
        self.save(experiment)
        for case in experiment['cases']:
            if case['status'] == 'dispatching':
                attempt = case['attempts'][-1]
                machine = self.machine(attempt['machine'])
                if machine.get('online'):
                    self.submit('start-' + attempt['id'], 'start', attempt['id'],
                        lambda m=machine, c=case['id'], a=copy.deepcopy(attempt): self.remote.call(m, 'start',
                            experiment=experiment['id'], case=c, attempt=a['id'], cpus=a['cpus'], memory=a['memory'], generation=experiment.get('generation', 0)))
        remaining = [c for c in experiment['cases'] if c['status'] != 'completed']
        failed_preparation = any(p['status'] in ('failed', 'stopped') for p in experiment['preparations'].values())
        preparing = any(p['status'] in ('preparing', 'running', 'accepted', 'transferring', 'pending', 'copying_sdk') for p in experiment['preparations'].values())
        if not remaining and not failed_preparation and not preparing:
            experiment.update(status='completed', finished=time.time())
            event(self.store, f"{experiment['name']}: completed", notify=True)
        elif not any(c['status'] in BUSY for c in remaining) and (
                all(c['status'] in {'failed', 'collection_failed'} for c in remaining) or (failed_preparation and not ready)):
            experiment['status'] = 'needs_attention'
        self.save(experiment)

    def state(self):
        machines = self.store.all('machines')
        experiments = [estimates(e, machines) for e in self.store.all('experiments')]
        return dict(machines=machines, experiments=experiments,
            settings=self.store.get('settings', 'main'), events=self.store.all('events')[-100:],
            commands=self.store.all('commands')[-20:], time=time.time())
