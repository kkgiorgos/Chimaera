import copy
import io
import json
from pathlib import Path
import tarfile
import time

import pytest

from common import atomic_json
from controller import Controller, estimates, safe_extract, validate_config, validate_machine

SDK_ID = 'sha256:' + 'a' * 64
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


    def copy_image(self, source, destination, image, cancel=None, progress=None):
        self.calls.append((destination['id'], 'copy_image', dict(source=source['id'], image=image)))
        return image


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
        config={}, source_identity={}, machines=['m0', 'm1', 'm2'], calibration={'records': {}, 'provenance': {'sdk_image_id': SDK_ID}},
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


def test_memory_budget_warning_is_persisted_and_notified_once(controller):
    value = experiment(controller, count=1)
    value['cases'][0].update(status='running', attempts=[dict(id='a', machine='m0', status='running')])
    controller.save(value)
    task = dict(id='a', experiment='e1', kind='case', status='running', output='/remote/a',
                memory_budget_bytes=2 * 1024**3, memory_peak_bytes=3 * 1024**3,
                memory_warning='RAM budget exceeded; increase RAM for future runs.')
    machine = controller.machine('m0')
    controller.ingest(machine, dict(tasks=[task]))
    controller.ingest(machine, dict(tasks=[task]))
    attempt = controller.experiment('e1')['cases'][0]['attempts'][0]
    assert attempt['status'] == 'running'
    assert attempt['memory_peak_bytes'] == 3 * 1024**3
    assert attempt['memory_warning'] == task['memory_warning']
    warnings = [e for e in controller.store.all('events') if e['level'] == 'warning']
    assert len(warnings) == 1
    assert warnings[0]['notify']
    assert 'Machine 0' in warnings[0]['message']
    task.pop('memory_warning')
    task.pop('memory_peak_bytes')
    controller.ingest(machine, dict(tasks=[task]))
    assert controller.experiment('e1')['cases'][0]['attempts'][0]['memory_warning']
    assert len([e for e in controller.store.all('events') if e['level'] == 'warning']) == 1


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
                ready=dict(records={'stream': dict(sustainable_gbps=40)}, provenance=dict(sdk_image_id=SDK_ID))))
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

    def fetch(self, machine, path, destination, cancel=None):
        with tarfile.open(destination, 'w:gz') as archive:
            if path.endswith('/preparation-report'):
                files = {'worker.log': 'prepared'}
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
        raise AssertionError('Guest assets must never be transferred between machines')


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


class BrokenCollection(FakeSSH):
    def fetch(self, machine, path, destination, cancel=None):
        raise RuntimeError('Remote output directory is missing')


def terminal_case(controller, outcome='failed'):
    value = experiment(controller, count=1)
    value['cases'][0].update(status='running', attempts=[dict(id='attempt', machine='m0',
        started=1, status='running', cpus=1, memory='2g')])
    controller.save(value)
    snapshot = dict(tasks=[dict(id='attempt', kind='case', experiment='e1', status=outcome,
                               output='/missing', finished=2)])
    return snapshot


def test_failed_case_collection_failure_is_terminal_and_never_loops(controller):
    controller.remote = BrokenCollection()
    snapshot = terminal_case(controller)
    controller.ingest(controller.machine('m0'), snapshot)
    assert controller.experiment('e1')['cases'][0]['status'] == 'failed'
    finish_jobs(controller)
    value = controller.experiment('e1')
    assert value['cases'][0]['attempts'][0]['collection_status'] == 'failed'
    controller.ingest(controller.machine('m0'), snapshot)
    assert not controller.jobs
    controller.schedule(controller.experiment('e1'))
    assert controller.experiment('e1')['status'] == 'needs_attention'
    controller.apply(dict(kind='retry', body=dict(id='e1')))
    assert controller.experiment('e1')['cases'][0]['status'] == 'queued'


def test_successful_execution_with_failed_collection_can_be_deleted(controller):
    controller.remote = BrokenCollection()
    snapshot = terminal_case(controller, 'completed')
    controller.ingest(controller.machine('m0'), snapshot)
    finish_jobs(controller)
    value = controller.experiment('e1')
    assert value['cases'][0]['status'] == 'collection_failed'
    controller.ingest(controller.machine('m0'), snapshot)
    assert not controller.jobs
    controller.apply(dict(kind='delete', body=dict(id='e1')))
    controller.ingest(controller.machine('m0'), snapshot)
    assert controller.experiment('e1')['status'] == 'deleted'
    assert not (controller.data / 'results/e1').exists()
    assert not controller.jobs


def test_preparation_log_collection_failure_does_not_repeat(controller):
    controller.remote = BrokenCollection()
    experiment(controller, count=1)
    snapshot = dict(tasks=[dict(id='e1-prepare', kind='prepare', experiment='e1',
                               status='failed', output='/missing', started=1)])
    controller.ingest(controller.machine('m0'), snapshot)
    finish_jobs(controller)
    controller.ingest(controller.machine('m0'), snapshot)
    assert controller.experiment('e1')['preparations']['m0']['collection_status'] == 'failed'
    assert not controller.jobs


def test_delete_cancels_inflight_collection_and_late_worker_cannot_resurrect_files(controller):
    import threading
    started, finish = threading.Event(), threading.Event()

    class SlowCollection(FakeSSH):
        def fetch(self, machine, path, destination, cancel=None):
            started.set()
            finish.wait(3)
            # Emulate a transport completing just as deletion cancels it.
            with tarfile.open(destination, 'w:gz'):
                pass

    controller.remote = SlowCollection()
    controller.ingest(controller.machine('m0'), terminal_case(controller))
    assert started.wait(1)
    try:
        before = time.monotonic()
        controller.apply(dict(kind='delete', body=dict(id='e1')))
        assert time.monotonic() - before < 1
        assert controller.cancel_collection('e1').is_set()
    finally:
        finish.set()
        finish_jobs(controller)
    assert not (controller.data / 'results/e1').exists()
    assert not (controller.data / 'collections/e1').exists()
    assert controller.experiment('e1')['status'] == 'deleted'


def test_explicit_collection_retry_preserves_case_attempt(controller):
    controller.remote = SimulatedCluster()
    snapshot = terminal_case(controller, 'completed')
    value = controller.experiment('e1')
    value['cases'][0]['status'] = 'collection_failed'
    value['cases'][0]['attempts'][0].update(status='completed', collection_status='failed', collection_error='lost reply')
    controller.save(value)
    controller.remote.hosts['m0'] = {'attempt': dict(snapshot['tasks'][0], case='case-000')}
    controller.apply(dict(kind='retry_collection', body=dict(id='e1')))
    controller.ingest(controller.machine('m0'), snapshot)
    finish_jobs(controller)
    value = controller.experiment('e1')
    assert value['cases'][0]['status'] == 'completed'
    assert len(value['cases'][0]['attempts']) == 1
    assert value['cases'][0]['attempts'][0]['collected']
    assert not any(call[1] == 'start' for call in controller.remote.calls)


def test_upgrade_recovers_failed_collecting_case_without_remote_machine(controller):
    value = experiment(controller, count=1)
    value['cases'][0].update(status='collecting', attempts=[dict(id='a', status='failed')])
    controller.save(value)
    reopened = Controller(ROOT, controller.data, FakeSSH())
    try:
        assert reopened.experiment('e1')['cases'][0]['status'] == 'failed'
        reopened.apply(dict(kind='delete', body=dict(id='e1')))
        assert reopened.experiment('e1')['results_deleted']
    finally:
        reopened.close()


def test_remote_cleanup_can_explicitly_discard_uncollected_failure_output(controller):
    value = experiment(controller, count=1)
    value.update(status='deleted', results_deleted=True)
    value['cases'][0]['attempts'] = [dict(id='a', collected=False)]
    controller.save(value)
    controller.apply(dict(kind='cleanup', body=dict(id='e1', discard_uncollected=True)))
    finish_jobs(controller)
    assert controller.experiment('e1')['remote_cleaned']


def test_existing_transferring_preparation_copies_sdk_before_local_build(controller):
    value = experiment(controller, count=1)
    value['preparations']['m1'] = dict(status='transferring')
    controller.schedule(value)
    finish_jobs(controller)
    assert any(call[0] == 'm1' and call[1] == 'copy_image' for call in controller.remote.calls)
    controller.schedule(controller.experiment('e1'))
    finish_jobs(controller)
    calls = [call for call in controller.remote.calls if call[1] == 'prepare']
    assert calls[0][0] == 'm1'
    assert not any(call[1] == 'asset_destination' for call in controller.remote.calls)
    assert not any(kind == 'transfer' for kind, _, _ in controller.jobs.values())


def test_server_assigns_id_for_third_machine_without_browser_uuid(controller):
    key = controller.command('machine', dict(id='', name='New machine', target='user@new', repo='/repo'))
    command = controller.store.get('commands', key)
    controller.apply(command)
    assert len(controller.store.all('machines')) == 4
    assert command['body']['id']


def test_selected_calibration_machine_prepares_first_and_supplies_sdk(controller):
    value = experiment(controller, count=1)
    value.update(status='preparing', calibration=None, calibration_machine='m2', preparations={})
    controller.save(value)
    controller.schedule(value)
    finish_jobs(controller)
    preparations = [c for c in controller.remote.calls if c[1] == 'prepare']
    assert [c[0] for c in preparations] == ['m2']
    assert preparations[0][2]['calibration'] is None
    ready = dict(records={'stream': dict(sustainable_gbps=40)}, provenance=dict(sdk_image_id=SDK_ID))
    controller.ingest(controller.machine('m2'), dict(tasks=[dict(id='e1-prepare', experiment='e1',
        kind='prepare', status='completed', ready=ready, output='/remote', collected=True)]))
    value = controller.experiment('e1')
    controller.schedule(value)
    # Consumers cannot prepare until the controller has reconciled transfer completion.
    assert len([c for c in controller.remote.calls if c[1] == 'prepare']) == 1
    finish_jobs(controller)
    transfers = [c for c in controller.remote.calls if c[1] == 'copy_image']
    assert {c[0] for c in transfers} == {'m0', 'm1'}
    assert all(c[2] == dict(source='m2', image=SDK_ID) for c in transfers)
    controller.schedule(controller.experiment('e1'))
    finish_jobs(controller)
    preparations = [c for c in controller.remote.calls if c[1] == 'prepare']
    assert {c[0] for c in preparations if c[2]['calibration']} == {'m0', 'm1'}
    assert all(c[2]['calibration']['provenance']['sdk_image_id'] == SDK_ID
               for c in preparations if c[2]['calibration'])


def test_sdk_transfer_failure_is_retryable_preparation_failure(controller):
    value = experiment(controller, count=1)
    value['preparations']['m1'] = dict(status='pending')
    def fail(*args, **kwargs):
        raise RuntimeError('SSH transfer interrupted')
    controller.remote.copy_image = fail
    controller.schedule(value)
    finish_jobs(controller)
    preparation = controller.experiment('e1')['preparations']['m1']
    assert preparation['status'] == 'failed'
    assert 'SSH transfer interrupted' in preparation['error']
    assert not any(c[0] == 'm1' and c[1] == 'prepare' for c in controller.remote.calls)
    controller.apply(dict(kind='retry_prepare', body=dict(id='e1', machine='m1')))
    finish_jobs(controller)
    assert 'm1' not in controller.experiment('e1')['preparations']


def test_cross_store_image_ids_stay_local_to_each_consumer(controller):
    value = experiment(controller, count=1)
    app_id = 'sha256:' + 'b' * 64
    value['calibration']['provenance']['application_image_id'] = app_id
    original = dict(value['calibration']['provenance'])
    local = {
        'm1': dict(sdk_image_id='sha256:' + 'c' * 64, application_image_id='sha256:' + 'd' * 64),
        'm2': dict(sdk_image_id='sha256:' + 'e' * 64, application_image_id='sha256:' + 'f' * 64),
    }
    transfers = []

    def transfer(source, destination, image, **kwargs):
        transfers.append((source['id'], destination['id'], image))
        field = 'sdk_image_id' if image == SDK_ID else 'application_image_id'
        return local[destination['id']][field]

    controller.remote.copy_image = transfer
    for machine in local:
        value['preparations'][machine] = dict(status='pending')
    controller.schedule(value)
    finish_jobs(controller)
    saved = controller.experiment('e1')
    assert saved['calibration']['provenance'] == original
    assert set(transfers) == {('m0', machine, image) for machine in local for image in (SDK_ID, app_id)}
    for machine, images in local.items():
        assert all(saved['preparations'][machine][key] == image for key, image in images.items())
    controller.schedule(saved)
    finish_jobs(controller)
    for machine, images in local.items():
        request = next(c[2] for c in controller.remote.calls if c[:2] == (machine, 'prepare'))
        assert request['calibration']['provenance'] == dict(original, **images)
        remembered = [c[2]['image'] for c in controller.remote.calls if c[:2] == (machine, 'remember_image')]
        assert set(images.values()) <= set(remembered)
    assert controller.experiment('e1')['calibration']['provenance'] == original


def test_stop_cancels_sdk_transfer_and_late_completion_cannot_prepare(controller):
    import threading
    value = experiment(controller, count=1)
    value['preparations']['m1'] = dict(status='copying_sdk')
    controller.save(value)
    cancelled = controller.sdk_cancellations.setdefault('e1', threading.Event())
    controller.apply(dict(kind='stop', body=dict(id='e1')))
    assert cancelled.is_set()
    controller.finished('sdk_transfer', ('e1', 'm1', 0), SDK_ID)
    assert controller.experiment('e1')['preparations']['m1']['status'] == 'stopped'


def test_queue_persists_calibration_choice_and_rejects_unselected_machine(controller, monkeypatch):
    import controller as module
    monkeypatch.setattr(module, 'validate_config', lambda *args: ({}, []))
    monkeypatch.setattr(module, 'source_identity', lambda *args: {})
    body = dict(name='Run', config={}, machines=['m0', 'm2'], calibration_machine='m2')
    key = controller.command('experiment', body)
    controller.apply(controller.store.get('commands', key))
    assert controller.experiment(key)['calibration_machine'] == 'm2'
    body['calibration_machine'] = 'm1'
    with pytest.raises(ValueError, match='Calibration machine'):
        controller.command('experiment', body)
    body.pop('calibration_machine')
    key = controller.command('experiment', body)
    controller.apply(controller.store.get('commands', key))
    assert controller.experiment(key)['calibration_machine'] == 'm0'


def test_resumed_experiment_ignores_old_sdk_transfer_results(controller):
    value = experiment(controller, count=1)
    value.update(generation=1)
    value['preparations']['m1'] = dict(status='copying_sdk')
    controller.save(value)
    controller.finished('sdk_transfer', ('e1', 'm1', 0), SDK_ID)
    controller.failed('sdk_transfer', ('e1', 'm1', 0), 'old transfer cancelled')
    assert controller.experiment('e1')['preparations']['m1']['status'] == 'copying_sdk'


@pytest.mark.parametrize('fail_application', [False, True])
def test_selected_machine_supplies_both_images_before_consumer_prepares(controller, fail_application):
    app_id = 'sha256:' + 'b' * 64
    value = experiment(controller, count=1)
    value['calibration']['provenance']['application_image_id'] = app_id
    value['preparations']['m1'] = dict(status='pending')
    copy_image = controller.remote.copy_image
    def copy(source, destination, image, **kwargs):
        if image == app_id and fail_application:
            raise RuntimeError('Application transfer interrupted')
        return copy_image(source, destination, image, **kwargs)
    controller.remote.copy_image = copy
    controller.schedule(value)
    finish_jobs(controller)
    preparation = controller.experiment('e1')['preparations']['m1']
    if fail_application:
        assert preparation['status'] == 'failed'
        assert 'Application transfer interrupted' in preparation['error']
    else:
        copies = [c for c in controller.remote.calls if c[1] == 'copy_image']
        assert [c[2]['image'] for c in copies] == [SDK_ID, app_id]
        controller.schedule(controller.experiment('e1'))
        finish_jobs(controller)
        assert any(c[0] == 'm1' and c[1] == 'prepare' for c in controller.remote.calls)


def test_cleanup_blocks_active_experiments_and_dispatch_while_cleaning(controller):
    value = experiment(controller, count=1)
    with pytest.raises(ValueError, match='Stop the experiment'):
        controller.apply(dict(kind='clean_assets', body=dict(id='m0')))
    value['status'] = 'stopped'
    controller.save(value)
    controller.apply(dict(kind='clean_assets', body=dict(id='m0')))
    assert controller.machine('m0')['cleaning']
    value['status'] = 'preparing'
    value['calibration'] = None
    value['preparations'] = {}
    controller.schedule(value)
    finish_jobs(controller)
    assert not any(c[1] in ('prepare', 'start') for c in controller.remote.calls)


def test_transfer_progress_reaches_state_and_survives_failure(controller):
    value = experiment(controller, count=1)
    value['preparations']['m1'] = dict(status='copying_sdk')
    controller.save(value)
    update = dict(phase='Streaming', bytes_sent=1234, bytes_per_second=100,
                  elapsed_seconds=12, updated=time.time())
    def transfer(source, destination, image, cancel=None, progress=None):
        progress(update)
        return image
    controller.remote.copy_image = transfer
    controller.copy_sdk(value, controller.machine('m1'), None)
    state = next(e for e in controller.state()['experiments'] if e['id'] == value['id'])
    assert state['preparations']['m1']['transfer']['bytes_sent'] == 1234
    assert state['preparations']['m1']['transfer']['image_kind'] == 'SDK'
    controller.failed('sdk_transfer', ('e1', 'm1', 0), 'destination disconnected')
    stored = controller.experiment('e1')['preparations']['m1']
    assert stored['transfer']['bytes_sent'] == 1234
    assert stored['status'] == 'failed'
    # Reports from the previous generation cannot appear on a resumed experiment.
    value = controller.experiment('e1')
    value['generation'] += 1
    value['preparations']['m1'] = dict(status='copying_sdk')
    controller.save(value)
    state = next(e for e in controller.state()['experiments'] if e['id'] == value['id'])
    assert 'transfer' not in state['preparations']['m1']
