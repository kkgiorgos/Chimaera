import copy
import io
import json
from pathlib import Path
import tarfile
import time

import pytest

from common import atomic_json
from controller import Controller, estimates, safe_extract, validate_config, validate_machine

ROOT = Path(__file__).resolve().parents[2]


class FakeSSH:
    def __init__(self):
        self.calls = []
        self.tasks = {}
        self.lose_reply = False

    def call(self, machine, method, **params):
        self.calls.append((machine['id'], method, params))
        if method == 'start':
            task = self.tasks.setdefault(params['attempt'], dict(id=params['attempt'], kind='case',
                experiment=params['experiment'], status='running', output='/remote/attempt'))
            if self.lose_reply:
                self.lose_reply = False
                raise ConnectionError('reply lost after acceptance')
            return task
        if method == 'snapshot':
            return dict(tasks=list(self.tasks.values()), stats=[])
        if method == 'stop':
            for task in self.tasks.values():
                if task['experiment'] == params['experiment']:
                    task['status'] = 'stopped'
        return True


@pytest.fixture
def controller(tmp_path):
    instance = Controller(ROOT, tmp_path, FakeSSH())
    for i in range(3):
        machine = validate_machine(dict(id=f'm{i}', name=f'Machine {i}', target=f'user@node{i}', repo='/repo', workers=2))
        instance.store.put('machines', machine['id'], dict(machine, deployed=True, online=True, seen=time.time()))
    yield instance
    instance.close()


def experiment(controller, key='e1', count=8):
    value = dict(id=key, name=key, status='running', created=time.time(), generation=0,
        config={}, source_identity={}, machines=['m0', 'm1', 'm2'], calibration={'records': {}},
        calibration_machine='m0', preparations={f'm{i}': dict(status='ready') for i in range(3)},
        cases=[dict(id=f'case-{i:03d}', point={}, status='queued', attempts=[]) for i in range(count)])
    controller.save(value)
    return value


def finish_jobs(controller):
    for kind, context, future in list(controller.jobs.values()):
        try:
            future.result(timeout=3)
        except Exception:
            pass
    for key, (kind, context, future) in list(controller.jobs.items()):
        if future.done():
            del controller.jobs[key]
            try:
                controller.finished(kind, context, future.result())
            except Exception as error:
                controller.failed(kind, context, str(error))


def test_dynamic_dispatch_fills_all_hosts(controller):
    value = experiment(controller)
    controller.schedule(value)
    finish_jobs(controller)
    starts = [call for call in controller.remote.calls if call[1] == 'start']
    assert len(starts) == 6
    assert {m: sum(call[0] == m for call in starts) for m in ['m0', 'm1', 'm2']} == dict(m0=2, m1=2, m2=2)
    assert all(call[2]['cpus'] == 1 and call[2]['memory'] == '2g' for call in starts)
    # Host 2 finishes first and immediately receives the next unassigned case.
    value = controller.experiment('e1')
    value['cases'][4]['status'] = 'completed'
    controller.schedule(value)
    finish_jobs(controller)
    assert value['cases'][6]['attempts'][-1]['machine'] == 'm2'


def test_lost_dispatch_reply_keeps_same_attempt(controller):
    value = experiment(controller, count=1)
    controller.remote.lose_reply = True
    controller.schedule(value)
    finish_jobs(controller)
    attempt = controller.experiment('e1')['cases'][0]['attempts'][0]['id']
    controller.schedule(controller.experiment('e1'))
    finish_jobs(controller)
    assert list(controller.remote.tasks) == [attempt]
    assert len(controller.experiment('e1')['cases'][0]['attempts']) == 1


def test_restart_recovers_dispatch_reservation(controller):
    value = experiment(controller, count=1)
    controller.schedule(value)
    finish_jobs(controller)
    second = Controller(ROOT, controller.data, controller.remote)
    try:
        second.schedule(second.experiment('e1'))
        finish_jobs(second)
        assert len(controller.remote.tasks) == 1
    finally:
        second.close()


def test_failed_case_never_retries_automatically_and_holds_queue(controller):
    value = experiment(controller, count=1)
    value['cases'][0]['status'] = 'failed'
    controller.schedule(value)
    finish_jobs(controller)
    assert controller.experiment('e1')['status'] == 'needs_attention'
    assert not controller.remote.tasks
    queued = experiment(controller, key='e2', count=1)
    queued['status'] = 'queued'
    controller.save(queued)
    controller.tick()
    assert controller.experiment('e2')['status'] == 'queued'


def test_manual_retry_targets_only_failed_case(controller):
    value = experiment(controller, count=2)
    value['status'] = 'needs_attention'
    value['cases'][0]['status'] = 'completed'
    value['cases'][1]['status'] = 'failed'
    controller.save(value)
    controller.apply(dict(kind='retry', body=dict(id='e1', case='case-001')))
    value = controller.experiment('e1')
    assert [c['status'] for c in value['cases']] == ['completed', 'queued']


def test_cpu_edits_apply_to_future_cases(controller):
    value = experiment(controller, count=8)
    controller.schedule(value)
    finish_jobs(controller)
    machine = controller.machine('m0')
    machine.update(cpus=3, memory='6g')
    controller.apply(dict(kind='machine', body=machine))
    value = controller.experiment('e1')
    value['cases'][0]['status'] = 'completed'
    controller.schedule(value)
    finish_jobs(controller)
    assert value['cases'][6]['attempts'][0]['cpus'] == 3
    assert value['cases'][1]['attempts'][0]['cpus'] == 1


def test_stop_waits_for_unreachable_host_and_resume_increments_generation(controller):
    value = experiment(controller, count=1)
    value.update(status='stopping', stop_acks=['m0', 'm1'])
    controller.save(value)
    controller.schedule(value)
    assert controller.experiment('e1')['status'] == 'stopping'
    assert controller.experiment('e1')['stop_pending'] == ['m2']
    finish_jobs(controller)
    controller.schedule(controller.experiment('e1'))
    assert controller.experiment('e1')['status'] == 'stopped'
    controller.apply(dict(kind='resume', body=dict(id='e1')))
    assert controller.experiment('e1')['generation'] == 1


def test_cleanup_requires_collected_attempts(controller):
    value = experiment(controller, count=1)
    value['status'] = 'stopped'
    value['cases'][0]['attempts'] = [dict(id='a', collected=False)]
    controller.save(value)
    with pytest.raises(ValueError, match='Collect every attempt'):
        controller.apply(dict(kind='cleanup', body=dict(id='e1')))


def test_configuration_validation_expands_current_template():
    source = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    config = json.loads(source.read_text())
    config['ramulator_config'] = 'gem5/ext/ramulator2/configs/lpddr5-6400-orin.json'
    portable, points = validate_config(ROOT, config)
    assert len(points) == 10
    assert len([p for p in points if p['pressure'] == 0]) == 1
    assert portable['ramulator_config'].startswith('gem5/')
    config['ramulator_config'] = '/etc/passwd'
    with pytest.raises(ValueError):
        validate_config(ROOT, config)


@pytest.mark.parametrize('change', [dict(target='-oProxyCommand=bad'), dict(repo='relative'),
    dict(cpus=0), dict(cpus=float('nan')), dict(memory='0g'), dict(workers=-1), dict(port=0)])
def test_invalid_machine_rejected(change):
    with pytest.raises(ValueError):
        validate_machine(dict(dict(id='m', name='Machine', target='user@host', repo='/repo'), **change))


def test_result_extraction_rejects_traversal_and_links(tmp_path):
    for name, kind in [('../../escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)]:
        archive = tmp_path / 'bad.tar'
        with tarfile.open(archive, 'w') as stream:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = '/etc/passwd'
            stream.addfile(info)
        with pytest.raises(ValueError, match='Unsafe'):
            safe_extract(archive, tmp_path / 'out')


def test_etas_unknown_until_measured_and_use_matching_worker_size(controller):
    value = experiment(controller, count=2)
    machines = controller.store.all('machines')
    assert estimates(copy.deepcopy(value), machines)['eta_seconds'] is None
    value['cases'][0].update(status='completed', attempts=[dict(id='a', machine='m0', cpus=1,
        memory='2g', started=10, finished=110, status='completed')])
    assert estimates(copy.deepcopy(value), machines, now=120)['eta_seconds'] == 100
    for machine in machines:
        machine['cpus'] = 4
    assert estimates(value, machines)['eta_seconds'] is None


def test_first_machine_is_only_calibration_producer(controller):
    value = experiment(controller, count=1)
    value.update(calibration=None, preparations={}, status='preparing')
    controller.schedule(value)
    finish_jobs(controller)
    preparations = [c for c in controller.remote.calls if c[1] == 'prepare']
    assert [call[0] for call in preparations] == ['m0']


def test_preparation_dispatch_can_be_reconciled_after_lost_reply(controller):
    value = experiment(controller, count=1)
    value.update(calibration=None, preparations={'m0': dict(status='preparing')}, status='preparing')
    controller.schedule(value)
    finish_jobs(controller)
    assert any(call[1] == 'prepare' for call in controller.remote.calls)


def test_result_collection_does_not_reserve_worker_capacity(controller):
    value = experiment(controller, count=8)
    controller.schedule(value)
    finish_jobs(controller)
    value = controller.experiment('e1')
    value['cases'][0]['status'] = 'collecting'
    controller.schedule(value)
    finish_jobs(controller)
    assert value['cases'][6]['attempts'][0]['machine'] == 'm0'


def test_resume_choice_survives_collection_of_prior_stopped_attempt(controller):
    value = experiment(controller, count=1)
    value.update(status='stopped')
    value['cases'][0].update(status='stopped', attempts=[dict(id='prior', status='stopped',
        machine='m0', started=1, cpus=1, memory='2g')])
    controller.save(value)
    controller.apply(dict(kind='resume', body=dict(id='e1')))
    controller.ingest(controller.machine('m0'), dict(tasks=[dict(id='prior', kind='case',
        experiment='e1', status='stopped', output='/remote', finished=2)]))
    assert controller.experiment('e1')['cases'][0]['status'] == 'queued'
    # Collection acknowledgement must not undo the user's resume selection either.
    controller.finished('collect', ('e1', 'case-000', 'prior'), True)
    assert controller.experiment('e1')['cases'][0]['status'] == 'queued'


def test_preparation_failure_notifies_before_diagnostics_are_collected(controller):
    value = experiment(controller, count=1)
    controller.ingest(controller.machine('m0'), dict(tasks=[dict(id='e1-prepare', kind='prepare',
        experiment='e1', status='failed', output='/remote', finished=2)]))
    assert any('preparation failed' in e['message'] and e['notify'] for e in controller.store.all('events'))


def test_replayed_create_command_does_not_duplicate_experiment(controller):
    source = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    config = json.loads(source.read_text())
    config['ramulator_config'] = 'gem5/ext/ramulator2/configs/lpddr5-6400-orin.json'
    _, points = validate_config(ROOT, config)
    command = dict(id='deterministic', kind='experiment', body=dict(name='Run', config=config, points=points, machines=['m0']))
    controller.apply(command)
    controller.apply(command)
    assert len(controller.store.all('experiments')) == 1


class SimulatedCluster(FakeSSH):
    """Runs the real scheduling/collection code against an in-memory SSH peer."""
    def __init__(self):
        super().__init__()
        self.hosts = {}

    def call(self, machine, method, **params):
        self.calls.append((machine['id'], method, params))
        host = self.hosts.setdefault(machine['id'], {})
        if method == 'prepare':
            key = params['experiment'] + '-prepare'
            host.setdefault(key, dict(id=key, kind='prepare', experiment=params['experiment'],
                status='completed', output='/remote/' + key, started=1,
                ready=dict(records={'stream': dict(sustainable_gbps=40)}, provenance={})))
            return host[key]
        if method == 'start':
            key = params['attempt']
            host.setdefault(key, dict(id=key, case=params['case'], kind='case', experiment=params['experiment'],
                status='running', output='/remote/' + key, started=time.time()))
            return host[key]
        if method == 'asset_destination':
            return '/remote/assets'
        if method == 'snapshot':
            return dict(tasks=list(host.values()), stats=[])
        return True

    def fetch(self, machine, path, destination):
        with tarfile.open(destination, 'w:gz') as archive:
            if path.endswith('/preparation-report'):
                files = {'worker.log': 'prepared'}
            elif path.endswith('/export-assets'):
                files = {'disk.img': 'guest', 'kernel': 'kernel'}
            else:
                task = next(t for t in self.hosts[machine['id']].values() if t['output'] == path)
                point = dict(id=task['case'], status='complete', trials=[task['case'] + '-attempt-001'])
                files = {'worker.log': 'finished', 'suite/experiment_manifest.json': json.dumps(dict(points=[point])),
                    'suite/dashboard.html': '<script>window.EXPERIMENT_DATA = ' + json.dumps(dict(models={}, trials=[], points=[point])) + ';</script>'}
            for name, content in files.items():
                body = content.encode()
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))

    def push(self, machine, source, destination):
        assert source.is_file()


def test_complete_distributed_workflow_with_one_frozen_calibration(controller):
    controller.remote = SimulatedCluster()
    value = experiment(controller, count=10)
    value.update(status='preparing', calibration=None, preparations={})
    controller.save(value)
    for _ in range(30):
        for machine in controller.store.all('machines'):
            controller.ingest(machine, controller.remote.call(machine, 'snapshot'))
        finish_jobs(controller)
        value = controller.experiment('e1')
        if value['status'] == 'completed':
            break
        controller.schedule(value)
        finish_jobs(controller)
        # Complete work after dispatch, preserving asynchronous collection behavior.
        for host in controller.remote.hosts.values():
            for task in host.values():
                if task['kind'] == 'case':
                    task.update(status='completed', finished=time.time())
    value = controller.experiment('e1')
    assert value['status'] == 'completed', value
    assert all(c['status'] == 'completed' and len(c['attempts']) == 1 and c['attempts'][0]['collected'] for c in value['cases'])
    assert all(p['collected'] for p in value['preparations'].values())
    preparations = [call for call in controller.remote.calls if call[1] == 'prepare']
    producers = {call[0] for call in preparations if call[2]['calibration'] is None}
    assert producers == {'m0'}
    assert {call[0] for call in preparations if call[2]['calibration']} == {'m1', 'm2'}
    assert len(list((controller.data / 'results/e1/attempts').iterdir())) == 10


def test_resume_waits_for_pending_collection_and_preserves_completed_result(controller):
    value = experiment(controller, count=1)
    value['status'] = 'stopped'
    value['cases'][0].update(status='collecting', attempts=[dict(id='a', machine='m0', status='completed',
        started=1, finished=2, cpus=1, memory='2g')])
    controller.save(value)
    controller.apply(dict(kind='resume', body=dict(id='e1')))
    assert controller.experiment('e1')['cases'][0]['status'] == 'collecting'
    controller.finished('collect', ('e1', 'case-000', 'a'), True)
    assert controller.experiment('e1')['cases'][0]['status'] == 'completed'


def test_resume_queues_interrupted_case_after_its_collection(controller):
    value = experiment(controller, count=1)
    value['status'] = 'stopped'
    value['cases'][0].update(status='collecting', attempts=[dict(id='a', machine='m0', status='stopped',
        started=1, finished=2, cpus=1, memory='2g')])
    controller.save(value)
    controller.apply(dict(kind='resume', body=dict(id='e1')))
    controller.finished('collect', ('e1', 'case-000', 'a'), True)
    assert controller.experiment('e1')['cases'][0]['status'] == 'queued'


def test_stop_is_not_queued_behind_transfers_or_dispatch_calls(controller):
    import threading
    release = threading.Event()
    try:
        for index in range(3):
            controller.submit(f'io-{index}', 'transfer', ('e', str(index)), lambda: release.wait(5))
        for index in range(12):
            controller.submit(f'rpc-{index}', 'start', str(index), lambda: release.wait(5))
        controller.submit('urgent-stop', 'stop', ('e', 'm0'), lambda: 'stopped')
        assert controller.jobs['urgent-stop'][2].result(timeout=1) == 'stopped'
    finally:
        release.set()
        for _, _, future in controller.jobs.values():
            future.result(timeout=2)
        controller.jobs.clear()
