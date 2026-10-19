import copy
import csv
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import struct
import unittest
from unittest.mock import Mock, patch

from chimaera_ros.latency import UNSET, driver_failure, guest_session, run_case, write_json
from chimaera_ros.latency_budget import wall_budget
from chimaera_ros.latency_config import DEFAULT, HARDWARE, plan_sweep
from chimaera_ros.latency_dashboard import Workbench, handler, samples, serve
from http.server import ThreadingHTTPServer


class SweepPlanTest(unittest.TestCase):
    def test_cpu_profiles_share_native_controls(self):
        config = copy.deepcopy(DEFAULT)
        config['gem5_configs'] += [dict(HARDWARE, name='Timing', cpu_type='timing'),
                                   dict(HARDWARE, name='O3', cpu_type='o3', num_cores=4)]
        plan = plan_sweep(config)
        self.assertEqual(plan['counts'], dict(native_free=1, native_stepped=2, chimaera=12))
        self.assertEqual(len({case['id'] for case in plan['cases']}), 15)
        o3 = [case['config'] for case in plan['cases'] if case['config'].get('cpu_type') == 'o3']
        self.assertTrue(all(case['num_cores'] == 4 for case in o3))

    def test_invalid_inputs_and_large_plans_fail_before_execution(self):
        for changes in [dict(rate_hz=[float('nan')]), dict(modes=[]), dict(payload_bytes=[True]),
                dict(poll_us=[50000]), dict(interval_us=[21001]), dict(repeats=0),
                dict(gem5_configs=[dict(HARDWARE, cpu_type='custom')]),
                dict(gem5_configs=[dict(HARDWARE, l1d_size='1000B')]),
                dict(gem5_configs=[dict(HARDWARE, cpu_clock='0GHz')]),
                dict(gem5_configs=[dict(HARDWARE, name='A'), dict(HARDWARE, name='A')]),
                dict(payload_bytes=list(range(64)), rate_hz=list(range(1, 65)), repeats=2),
                dict(command='unrecognized')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan_sweep(dict(DEFAULT, **changes))

    def test_deduplication_and_invalid_timing_pairs(self):
        result = plan_sweep(dict(DEFAULT, payload_bytes=[64, 64], poll_us=[10000, 50000]))
        self.assertEqual(result['counts']['chimaera'], 2)
        self.assertEqual(len(result['skipped_pairs']), 2)

    def test_detailed_guest_uses_instruction_m5ops(self):
        for cpu in ('kvm', 'timing', 'o3'):
            session = guest_session(cpu)
            self.assertEqual(session['bridge']['m5ops'], 'address' if cpu == 'kvm' else 'instruction')
            self.assertTrue(session['bridge']['simulation_time_from_readfile'])

    def test_fine_steps_warn_without_changing_requested_deadline(self):
        result = plan_sweep(dict(DEFAULT, modes=['native_stepped'], interval_us=[2000],
            poll_us=[200], duration=10, warmup=.1, drain=.2, timeout=300))
        case = result['cases'][0]
        self.assertEqual(case['config']['timeout'], 300)
        self.assertEqual(case['wall_budget'], dict(synchronization_steps=5150,
            settling_floor_seconds=206., estimated_seconds=525., recommended_timeout=840))
        self.assertTrue(any('5150 steps' in warning and '300 s timeout' in warning
                            for warning in result['warnings']))
        self.assertEqual(result['recommended_timeout'], 840)
        free = dict(case['config'], mode='native_free')
        self.assertEqual(wall_budget(free)['synchronization_steps'], 0)

    def test_driver_failure_explains_timeout_and_preserves_early_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = dict(mode='native_stepped', interval_us=2000, timeout=300,
                          duration=10, warmup=.1, drain=.2)
            (directory/'driver.log').write_text('Latency driver: Gazebo step timed out\n')
            (directory/'physics-clock.bin').write_bytes(struct.pack('<QQ', 0x434c42434c4f434b, 5922000000))
            message = driver_failure(directory, config, 300.37)
            self.assertIn('300 s wall timeout', message)
            self.assertIn('5.922 / 10.3 simulated seconds', message)
            self.assertIn('840 s', message)
            self.assertNotIn('guest export', message)
            (directory/'driver.log').write_text('Latency driver: Transport startup failed\n')
            self.assertEqual(driver_failure(directory, config, 1),
                             'Transport startup failed; see driver.log')


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workbench = Workbench(self.root, self.root/'data')
        self.addCleanup(self.workbench.close)

    def fixture(self):
        directory = self.root/'imported'/'case'
        directory.mkdir(parents=True)
        config = plan_sweep(dict(DEFAULT, modes=['native_free']))['cases'][0]['config']
        config.update(status='complete', run_id='example')
        write_json(directory/'run.json', config)
        with (directory/'events.csv').open('w') as stream:
            writer = csv.writer(stream)
            writer.writerow(['side', 'event', 'sequence', 'scheduled_ns', 'publish_ns',
                             'callback_ns', 'physics_ns', 'payload_bytes'])
            writer.writerows([
                ['host', 'send', 0, 0, 0, UNSET, 0, 64],
                ['guest', 'receive', 0, 0, 0, 101000000, 0, 64],
                ['host', 'send', 1, 100000000, 100000000, UNSET, 0, 64],
                ['guest', 'receive', 1, 100000000, 100000000, 102000000, 0, 64],
                ['guest', 'receive', 1, 100000000, 100000000, 103000000, 0, 64],
                ['host', 'send', 2, 590000000, 590000000, UNSET, 0, 64],
                ['guest', 'receive', 2, 590000000, 590000000, 610000000, 0, 64]])
        return directory

    def test_import_is_read_only_and_survives_restart(self):
        directory = self.fixture()
        before = {path: path.read_bytes() for path in directory.iterdir()}
        job = self.workbench.import_results(str(directory.parent))
        self.assertEqual(before, {path: path.read_bytes() for path in directory.iterdir()})
        self.assertEqual(self.workbench.case_directory(job['id'], 'case_0001'), directory)
        restarted = Workbench(self.root, self.root/'data')
        self.assertEqual(restarted.job(job['id'])['status'], 'complete')
        self.assertEqual(restarted.report(job['id'])['runs'][0]['case_id'], 'case_0001')
        with self.assertRaises(ValueError):
            self.workbench.case_directory(job['id'], '../../anything')

    def test_distribution_uses_publish_window_and_unique_receipts(self):
        result = samples(self.fixture(), 'host_to_guest')
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['cdf'], [[2., .5], [20., 1.]])
        self.assertEqual(result['trace'][0], [0., 2.])

    def test_single_runner_remains_busy_until_stop_cleanup(self):
        entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()
        def executor(*args, cancelled, **kwargs):
            entered.set()
            while not cancelled():
                cleaning.wait(.01)
            cleaning.set()
            release.wait(3)
            raise KeyboardInterrupt('Stopped by user')
        self.workbench.executor = executor
        self.addCleanup(release.set)
        with patch.object(self.workbench, '_environment', return_value={}), patch.object(
                self.workbench, 'preflight', return_value=dict(ready_native=True)):
            job = self.workbench.start(dict(DEFAULT, modes=['native_free']))
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, 'One job'):
                self.workbench.start(dict(DEFAULT, modes=['native_free']))
            self.workbench.stop(job['id'])
            self.assertTrue(cleaning.wait(2))
            self.assertTrue(self.workbench.busy())
            release.set()
            self.workbench.worker.join(3)
        self.assertFalse(self.workbench.busy())
        self.assertEqual(self.workbench.job(job['id'])['status'], 'stopped')

    def test_service_restart_marks_unfinished_jobs_interrupted(self):
        job = self.workbench._new('sweep', 'unfinished')
        restarted = Workbench(self.root, self.root/'data')
        self.assertEqual(restarted.job(job['id'])['status'], 'interrupted')

    def failed_sweep(self):
        plan = plan_sweep(dict(DEFAULT, modes=['native_stepped'],
            interval_us=[10000, 5000, 2000], poll_us=[200], timeout=300))
        cases = [dict(case, status='pending') for case in plan['cases']]
        job = self.workbench._new('sweep', 'Resume fixture', plan['config'], cases)
        prefix = self.root/'prefix'
        artifacts = {}
        for name in ('lib/chimaera_ros_bridge/latency_endpoint', 'lib/libchimaera_latency_clock.so'):
            path = prefix/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'collector fixture')
            artifacts[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.workbench.runtime['prefix'] = str(prefix)
        root = Path(job['source'])
        for index, status in enumerate(('complete', 'failed')):
            case = cases[index]
            case['status'] = status
            directory = root/case['id']
            directory.mkdir()
            write_json(directory/'run.json', dict(case['config'], status=status,
                run_id=case['id'], artifact_sha256=artifacts))
            (directory/'events.csv').write_text('side,event,sequence,scheduled_ns,publish_ns,callback_ns,physics_ns,payload_bytes\n')
            (directory/'driver.log').write_text('original attempt\n')
        job.update(status='failed', error='original error')
        self.workbench._save(job)
        return job, root

    def test_resume_reuses_complete_cases_and_preserves_failed_attempt(self):
        job, root = self.failed_sweep()
        original = {path: path.read_bytes() for path in root.glob('case_*/*')}
        executed = []
        def executor(directory, config, *args, **kwargs):
            executed.append((directory, copy.deepcopy(config)))
            directory.mkdir()
            write_json(directory/'run.json', dict(config, status='complete', run_id=directory.name))
            (directory/'events.csv').write_text('side,event,sequence,scheduled_ns,publish_ns,callback_ns,physics_ns,payload_bytes\n')
        self.workbench.executor = executor
        with patch.object(self.workbench, 'preflight', return_value=dict(ready_native=True)), \
                patch.object(self.workbench, '_environment', return_value={}):
            self.workbench.resume(job['id'], 1200)
            self.workbench.worker.join(3)
        self.assertFalse(self.workbench.worker.is_alive())
        self.assertEqual(job['status'], 'complete', job.get('error'))
        self.assertEqual([path.name for path, _ in executed], ['case_0002_attempt_002', 'case_0003'])
        self.assertTrue(all(config['timeout']==1200 for _, config in executed))
        self.assertEqual(job['cases'][0]['config']['timeout'], 300)
        self.assertEqual(original, {path: path.read_bytes() for path in original})
        self.assertEqual(self.workbench.case_directory(job['id'], 'case_0002'), executed[0][0])
        report = self.workbench.report(job['id'])
        self.assertEqual(len(report['runs']), 3)
        self.assertEqual(len(report['excluded']), 1)
        self.assertEqual({run['case_id'] for run in report['runs']}, {case['id'] for case in job['cases']})
        self.assertEqual(job['resume_history'][0]['previous_timeout'], 300)
        self.assertEqual(job['resume_history'][0]['timeout'], 1200)
        restarted = Workbench(self.root, self.root/'data')
        self.assertEqual(restarted.case_directory(job['id'], 'case_0002'), executed[0][0])

    def test_resume_rejects_changed_collectors_before_mutating_saved_cases(self):
        job, _ = self.failed_sweep()
        before = copy.deepcopy(job)
        (Path(self.workbench.runtime['prefix'])/'lib/chimaera_ros_bridge/latency_endpoint').write_bytes(b'changed')
        with patch.object(self.workbench, 'preflight', return_value=dict(ready_native=True)):
            with self.assertRaisesRegex(ValueError, 'Collectors changed'):
                self.workbench.resume(job['id'], 1200)
        self.assertEqual(job, before)

    def test_resume_validates_deadlines_and_job_state(self):
        job, _ = self.failed_sweep()
        for timeout in (True, 0, -1, 1.5, 86401, '1200'):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, 'integer'):
                self.workbench.resume(job['id'], timeout)
        job['status'] = 'complete'
        with self.assertRaisesRegex(ValueError, 'Only failed'):
            self.workbench.resume(job['id'])
        job['status'] = 'failed'
        self.workbench.worker = Mock()
        self.workbench.worker.is_alive.return_value = True
        with self.assertRaisesRegex(ValueError, 'cleanup'):
            self.workbench.resume(job['id'])
        self.workbench.worker = None

    def test_build_stop_finishes_cleanup_before_unlocking_runner(self):
        entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()
        process = Mock(pid=123456)
        process.poll.return_value = None
        def launch(*args, **kwargs):
            self.assertIn('MAKEFLAGS="-j2 -l2"', args[0][-1])
            self.assertIn('console_direct+', args[0][-1])
            entered.set()
            return process
        def cleanup(*args):
            cleaning.set()
            release.wait(3)
        self.addCleanup(release.set)
        with patch.object(self.workbench, 'preflight', return_value=dict(can_build=True)), \
                patch('chimaera_ros.latency_dashboard.subprocess.Popen', side_effect=launch), \
                patch('chimaera_ros.latency_dashboard.os.killpg', side_effect=cleanup), \
                patch('chimaera_ros.latency_dashboard.stage_guest') as stage:
            job = self.workbench.prepare()
            self.assertTrue(entered.wait(2))
            self.workbench.stop(job['id'])
            self.assertTrue(cleaning.wait(2))
            self.assertTrue(self.workbench.busy())
            release.set()
            self.workbench.worker.join(3)
            stage.assert_not_called()
        self.assertEqual(self.workbench.job(job['id'])['status'], 'stopped')

    def test_server_obtains_lock_before_loading_job_state(self):
        with patch('chimaera_ros.latency_dashboard.fcntl.flock', side_effect=BlockingIOError), \
                patch('chimaera_ros.latency_dashboard.Workbench') as construct:
            with self.assertRaises(BlockingIOError):
                serve(self.root, self.root/'locked')
            construct.assert_not_called()

    def test_old_bundle_is_not_ready(self):
        import tarfile
        bundle = self.root/'empty.tar.gz'
        with tarfile.open(bundle, 'w:gz'):
            pass
        self.assertFalse(self.workbench._bundle_ready(str(bundle)))

    def test_hardware_and_guest_clock_reach_launch_configuration(self):
        package, gem5 = self.root/'package', self.root/'gem5'
        paths = [package/'lib/chimaera_ros_bridge/latency_endpoint',
                 package/'lib/chimaera_ros_bridge/latency_driver',
                 package/'lib/libchimaera_latency_clock.so',
                 gem5/'build/X86/gem5.opt', self.root/'disk', self.root/'kernel']
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test artifact')
        for cpu in ('kvm', 'timing', 'o3'):
            point = plan_sweep(dict(DEFAULT, modes=['chimaera'], interval_us=[50000],
                poll_us=[10000], gem5_configs=[dict(HARDWARE, cpu_type=cpu, name=cpu,
                    num_cores=4, cpu_clock='2GHz', l1d_size='32KiB', l2_size='1MiB')]))['cases'][0]['config']
            output = self.root/cpu
            # Fail at process creation, after manifests and exact commands exist.
            with patch('chimaera_ros.latency.os.access', return_value=True), \
                    patch('chimaera_ros.latency.subprocess.check_output', return_value='test'), \
                    patch('chimaera_ros.latency.subprocess.Popen', side_effect=OSError('launch sentinel')):
                with self.assertRaisesRegex(OSError, 'launch sentinel'):
                    run_case(output, point, package, gem5, self.root/'disk', self.root/'kernel')
            commands = dict(json.loads((output/'commands.json').read_text()))
            args = commands['gem5']
            for key, value in [('cpu-type', cpu), ('num-cores', '4'), ('cpu-clock', '2GHz'),
                               ('l1d-size', '32KiB'), ('l2-size', '1MiB')]:
                self.assertEqual(args[args.index('--'+key)+1], value)
            self.assertIn('libchimaera_gem5_clock' + ('_addr' if cpu == 'kvm' else '') + '.so',
                          (output/'guest.yaml').read_text())

    def test_http_guards_and_validation(self):
        web = Path(__file__).resolve().parents[1]/'latency_web'
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.workbench, web))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            def request(path, body, headers=None):
                connection.request('POST', path, json.dumps(body), headers or {})
                result = connection.getresponse()
                value = json.loads(result.read())
                return result.status, value
            self.assertEqual(request('/api/plan', DEFAULT)[0], 400)
            headers = {'X-Chimaera-Request':'1', 'Content-Type':'application/json'}
            code, value = request('/api/plan', DEFAULT, headers)
            self.assertEqual((code, value['total']), (200, 7))
            self.assertEqual(request('/api/plan', DEFAULT, dict(headers, Origin='http://external.test'))[0], 400)
            self.assertEqual(request('/api/plan', DEFAULT, dict(headers, Host='external.test'))[0], 400)
            self.assertEqual(request('/api/prepare', {'build':'false'}, headers)[0], 400)
            self.assertEqual(request('/api/stop', [], headers)[0], 400)
            connection.request('GET', '/%2e%2e/chimaera_ros/latency.py')
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_lan_host_keeps_same_origin_validation(self):
        web = Path(__file__).resolve().parents[1]/'latency_web'
        request = object.__new__(handler(self.workbench, web))
        request.server = Mock(server_port=8787)
        request.connection = Mock()
        request.connection.getsockname.return_value = ('192.168.1.2', 8787)
        request.headers = dict(Host='192.168.1.2:8787', Origin='http://192.168.1.2:8787')
        request.allowed()
        request.headers['Origin'] = 'http://external.test'
        with self.assertRaisesRegex(ValueError, 'Cross-origin'):
            request.allowed()

    def test_published_docker_address_is_explicit_and_keeps_origin_guard(self):
        request = object.__new__(handler(self.workbench, self.root))
        request.server = Mock(server_port=8787, public_address='192.168.1.2:9876')
        request.connection = Mock()
        request.connection.getsockname.return_value = ('172.18.0.2', 8787)
        request.headers = dict(Host='192.168.1.2:9876', Origin='http://192.168.1.2:9876')
        request.allowed()
        request.headers['Host'] = 'external.test:9876'
        with self.assertRaisesRegex(ValueError, 'workbench IP'):
            request.allowed()
        request.headers['Host'] = '192.168.1.2:9876'
        request.headers['Origin'] = 'http://external.test:9876'
        with self.assertRaisesRegex(ValueError, 'Cross-origin'):
            request.allowed()


if __name__ == '__main__':
    unittest.main()
