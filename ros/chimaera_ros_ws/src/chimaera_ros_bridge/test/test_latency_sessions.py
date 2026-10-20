import ctypes
import json
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import Mock, patch

from chimaera_ros.latency import driver_failure, run_case
from chimaera_ros.latency_config import DEFAULT, plan_sweep
from chimaera_ros.latency_guest import read_script
from chimaera_ros.latency_session import GuestSession, SweepSessions, ordered_cases, validate_bundle
from chimaera_ros.latency_startup import log_tail, startup_status


class SessionTest(unittest.TestCase):
    def test_groups_hardware_without_changing_case_identity_or_native_controls(self):
        config = dict(DEFAULT, payload_bytes=[64, 128], gem5_configs=[
            dict(name='KVM', cpu_type='kvm'), dict(name='Timing', cpu_type='timing')])
        cases = plan_sweep(config)['cases']
        ordered = ordered_cases(cases, True)
        self.assertEqual({case['id'] for case in ordered}, {case['id'] for case in cases})
        with patch.object(GuestSession, 'close') as close:
            with SweepSessions() as sessions:
                guests = [sessions.for_case(case['config']) for case in ordered]
            self.assertEqual(len({id(guest) for guest in guests if guest is not None}), 2)
            self.assertEqual(close.call_count, 2)
        self.assertIs(ordered_cases(cases, False), cases)
        with SweepSessions(False) as sessions:
            self.assertTrue(all(sessions.for_case(case['config']) is None for case in cases))

    def test_guest_readfile_chunks_and_kvm_barrier_overshoot(self):
        script = b'#!/bin/bash\n' + b'# comment\n' * 1000
        def read(buffer, size, offset):
            chunk = script[offset:offset+size]
            ctypes.memmove(buffer, chunk, len(chunk))
            return len(chunk)
        self.assertEqual(read_script(read), script)
        script = b'CHIMAERA_END_CASE\n'
        self.assertIsNone(read_script(read))
        script = b'CHIMAERA_TIME_ORIGIN_NS 123\n'
        with self.assertRaisesRegex(RuntimeError, 'Missing next-case'):
            read_script(read)

    def test_two_cases_launch_one_vm_and_inject_new_traffic_and_polling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, gem5 = root/'package', root/'gem5'
            for path in [package/'lib/chimaera_ros_bridge/latency_endpoint',
                         package/'lib/chimaera_ros_bridge/latency_driver',
                         package/'lib/libchimaera_latency_clock.so', gem5/'build/X86/gem5.opt',
                         root/'disk', root/'kernel']:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            config = plan_sweep(dict(DEFAULT, modes=['chimaera'], interval_us=[50000], dds_shm_bytes=16777216,
                poll_us=[1000]))['cases'][0]['config']
            bundle = root/'guest.tar.gz'
            with tarfile.open(bundle, 'w:gz') as archive:
                data = b'{"version":4,"transport_protocol":3}'
                member = tarfile.TarInfo('opt/chimaera/latency/latency-runtime.json')
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            launches = []
            vm = Mock(returncode=None)
            vm.poll.return_value = None
            def launch(command, **kwargs):
                launches.append(command)
                if command[0] == str(gem5/'build/X86/gem5.opt'):
                    return vm
                if command[0].endswith('latency_driver'):
                    output = Path(command[-1]).parent
                    (output/'complete').write_text('complete\n')
                process = Mock(returncode=0)
                process.poll.return_value = 0
                return process
            session = GuestSession()
            try:
                with patch('chimaera_ros.latency.os.access', return_value=True), \
                     patch('chimaera_ros.latency.subprocess.check_output', return_value='commit'), \
                     patch('chimaera_ros.latency.subprocess.Popen', side_effect=launch), \
                     patch('chimaera_ros.latency.summarize', return_value={}):
                    run_case(root/'first', config, package, gem5, root/'disk', root/'kernel', bundle, reuse=session)
                    run_case(root/'second', dict(config, payload_bytes=128, poll_us=5000),
                             package, gem5, root/'disk', root/'kernel', bundle, reuse=session)
                self.assertEqual(sum(command[0] == str(gem5/'build/X86/gem5.opt') for command in launches), 1)
                self.assertFalse(vm.wait.called)
                first = json.loads((root/'first/run.json').read_text())
                second = json.loads((root/'second/run.json').read_text())
                self.assertFalse(first['boot_reused'])
                self.assertTrue(second['boot_reused'])
                self.assertEqual(first['boot_session'], second['boot_session'])
                self.assertIn('poll_us: 5000', (root/'second/driver.yaml').read_text())
                self.assertIn('start_case: true', (root/'second/driver.yaml').read_text())
                self.assertIn('payload_bytes: 128', (root/'second/guest.yaml').read_text())
                self.assertEqual((root/'second/gem5').resolve(), root/'first/gem5')
                self.assertFalse((root/'second/gem5').readlink().is_absolute())
                self.assertIn('ready_file: "/tmp/chimaera-latency-endpoint-ready"',
                              (root/'second/guest.yaml').read_text())
                session_config = json.loads((root/'second/guest-session.json').read_text())
                self.assertEqual(session_config['bridge']['application_ready_file'],
                                 '/tmp/chimaera-latency-endpoint-ready')
                self.assertFalse(session_config['guest']['localhost_only'])
                self.assertEqual(session_config['guest']['env']['CHIMAERA_LATENCY_REUSE'], '1')
                self.assertEqual(session_config['guest']['env']['FASTRTPS_DEFAULT_PROFILES_FILE'],
                                 '/tmp/chimaera-latency-dds.xml')
                self.assertIn('/tmp/chimaera-latency-dds.xml', (root/'first/next-case.sh').read_text())
                self.assertIn('/tmp/chimaera-latency-endpoint-ready', (root/'first/next-case.sh').read_text())
                subprocess.run(['bash', '-n', str(root/'first/next-case.sh')], check=True)
                self.assertEqual(dict(json.loads((root/'second/commands.json').read_text()))['gem5'][0],
                                 'reuse-session')
            finally:
                vm.poll.return_value = 0
                session.close()

    def test_vm_cleanup_runs_on_failure(self):
        with patch.object(GuestSession, 'close') as close:
            with self.assertRaisesRegex(RuntimeError, 'case failed'):
                with SweepSessions() as sessions:
                    sessions.for_case(dict(mode='chimaera'))
                    raise RuntimeError('case failed')
            close.assert_called_once_with()

    def test_old_or_absent_bundles_fail_before_boot(self):
        with self.assertRaisesRegex(ValueError, 'v4'):
            validate_bundle(None)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'old.tar.gz'
            with tarfile.open(path, 'w:gz'):
                pass
            with self.assertRaisesRegex(ValueError, 'v4'):
                validate_bundle(path)
            # Benchmark schema v4 alone cannot establish transport compatibility.
            with tarfile.open(path, 'w:gz') as archive:
                data = b'{"version":4}'
                member = tarfile.TarInfo('opt/chimaera/latency/latency-runtime.json')
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, 'transport protocol 3'):
                validate_bundle(path)
        with self.assertRaisesRegex(ValueError, 'boolean'):
            plan_sweep(dict(DEFAULT, single_boot='true'))

    def test_preworkbegin_disk_failure_is_separate_from_measurement_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            serial = root/'gem5/board.pc.com_1.device'
            serial.parent.mkdir()
            serial.write_text('[ 33.0] ata1: lost interrupt (Status 0x50)\n'
                              '[ 33.1] ata1.00: failed command: READ DMA\n')
            config = dict(mode='chimaera', timeout=300, interval_us=2000,
                          duration=10, warmup=.1, drain=.2)
            message = driver_failure(root, config, 301)
            self.assertIn('during gem5_boot', message)
            self.assertIn('lost interrupt', message)
            self.assertIn('READ DMA', message)
            self.assertNotIn('Small synchronization steps', message)
            (root/'logical-clock.bin').write_bytes(struct.pack('<QQ', 0x434c42434c4f434b, 5922000000))
            message = driver_failure(root, config, 301)
            self.assertIn('5.922 / 10.3 simulated seconds', message)
            self.assertIn('Small synchronization steps', message)
            self.assertEqual(startup_status(root)['phase'], 'cosimulation')
            self.assertEqual(log_tail(serial, 9), 'READ DMA\n')


if __name__ == '__main__':
    unittest.main()
