"""Exercise the importable timing module with a deterministic gem5 backend."""
import argparse
from enum import Enum
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, Mock, patch


class Event(Enum):
    WORKBEGIN = 'workbegin'
    WORKEND = 'workend'
    EXIT = 'exit'
    MAX_TICK = 'max_tick'
    FAILED = 'failed'

    @classmethod
    def translate_exit_status(cls, cause):
        if not isinstance(cause, cls):
            raise NotImplementedError(f"Exit event '{cause}' not implemented")
        return cause


class SimulatorStub:
    def __init__(self, state, events, handlers):
        self.state = state
        self.events = iter(events)
        self.handlers = handlers
        self.budgets = []
        self.last = None

    def set_max_ticks(self, budget):
        self.budgets.append(budget)

    def run(self):
        self.last, advance = next(self.events)
        self.state[0] += advance
        if self.last in self.handlers:
            next(self.handlers[self.last])

    def get_last_exit_event_code(self):
        return 127

    def get_last_exit_event_cause(self):
        return self.last


class TimingTest(unittest.TestCase):
    def setUp(self):
        self.tick = [0]
        self.m5 = ModuleType('m5')
        self.m5.curTick = lambda: self.tick[0]
        self.m5.MaxTick = 2**64 - 1
        self.m5.options = Mock(outdir='/tmp/gem5')
        self.m5.stats = Mock()
        self.m5.ticks = Mock()
        event_module = ModuleType('gem5.simulate.exit_event')
        event_module.ExitEvent = Event
        source = Path(__file__).resolve().parents[1] / 'config/chimaera_gem5.py'
        spec = importlib.util.spec_from_file_location('chimaera_gem5_test', source)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'m5': self.m5, 'm5.ticks': self.m5.ticks,
                                     'gem5.simulate.exit_event': event_module}):
            spec.loader.exec_module(self.module)
        self.server = self.module.TimingServer()

    def simulator(self, events):
        simulator = SimulatorStub(self.tick, events, self.server.exit_handlers())
        self.server.simulator = simulator
        return simulator

    def test_import_has_no_simulation_or_tick_configuration_side_effects(self):
        self.m5.ticks.setGlobalFrequency.assert_not_called()
        self.m5.stats.reset.assert_not_called()
        self.module.configure_ticks()
        self.m5.ticks.setGlobalFrequency.assert_called_once_with('1THz')
        self.m5.ticks.fixGlobalFrequency.assert_called_once_with()

    def test_workbegin_prepares_experiment_once_before_reset(self):
        calls = []
        self.server.on_ready = lambda: calls.append('prepare')
        self.m5.stats.reset.side_effect = lambda: calls.append('reset')
        handler = self.server.on_workbegin()
        self.assertTrue(next(handler))
        self.assertTrue(next(handler))
        self.assertEqual(calls, ['prepare', 'reset'])
        self.assertTrue(self.server.roi_started)
        self.assertEqual(self.server.handle_command('QUIT'), 'BYE')
        self.assertTrue(self.server.exit_requested)
        self.m5.stats.dump.assert_called_once_with()

    def test_failed_roi_setup_never_marks_ready_or_resets_stats(self):
        self.server.on_ready = Mock(side_effect=RuntimeError('CPU mismatch'))
        with self.assertRaisesRegex(RuntimeError, 'CPU mismatch'):
            next(self.server.on_workbegin())
        self.assertFalse(self.server.roi_started)
        self.m5.stats.reset.assert_not_called()

    def test_intermediate_events_do_not_restart_step_budget(self):
        simulator = self.simulator([(Event.EXIT, 25), (Event.WORKBEGIN, 25),
                                    (Event.MAX_TICK, 50)])
        self.assertEqual(self.server.handle_command('STEP_TICKS 100'), 'OK 0 100')
        self.assertEqual(simulator.budgets, [100, 75, 50])

    def test_max_tick_reports_actual_progress_for_host_drift_correction(self):
        simulator = self.simulator([(Event.MAX_TICK, 98)])
        self.assertEqual(self.server.handle_command('STEP_TICKS 100'), 'OK 0 98')
        self.assertEqual(simulator.budgets, [100])

    def test_zero_tick_poll_does_not_advance_simulator(self):
        simulator = self.simulator([])
        self.assertEqual(self.server.handle_command('STEP_TICKS 0'), 'OK 0 0')
        self.assertEqual(simulator.budgets, [])

    def test_units_and_step_limits(self):
        self.server.run_for_ticks = Mock(return_value='OK')
        for command, expected in [('STEP_US 7', 7000000), ('STEP_NS 7', 7000),
                                  ('STEP_TICKS 7', 7)]:
            self.assertEqual(self.server.handle_command(command), 'OK')
            self.server.run_for_ticks.assert_called_with(expected)
        for command in ('STEP_US 0', 'STEP_NS -1', 'STEP_TICKS 1.0', 'STEP_TICKS ٢',
                        'STEP_TICKS 1 2', 'RESET'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.server.handle_command(command)
        server = self.module.TimingServer()
        with self.assertRaises(ValueError):
            server.run_for_ticks(3600000000000001)
        self.tick[0] = self.m5.MaxTick - 1
        with self.assertRaises(ValueError):
            server.run_for_ticks(1)

    def test_workend_and_unexpected_events_report_done(self):
        for event in (Event.WORKEND, Event.FAILED):
            self.server = self.module.TimingServer()
            self.tick[0] = 0
            self.simulator([(event, 10)])
            self.assertEqual(self.server.handle_command('STEP_TICKS 100'), 'DONE 10')
            self.assertEqual(self.server.handle_command('STATUS'), 'DONE 10')

    def test_boot_ready_precedes_serving(self):
        simulator = self.simulator([(Event.EXIT, 10), (Event.WORKBEGIN, 10)])
        def serve(path, *, managed_shutdown):
            self.assertTrue(self.server.roi_started)
            self.m5.stats.reset.assert_called_once_with()
            self.assertEqual(path, '/tmp/test.sock')
            self.assertTrue(managed_shutdown)
        self.server.serve = Mock(side_effect=serve)
        self.server.run(simulator, '/tmp/test.sock', managed_shutdown=True)
        self.server.serve.assert_called_once()

    def test_boot_failure_never_opens_timing_socket(self):
        simulator = self.simulator([(Event.FAILED, 10)])
        self.server.serve = Mock()
        with self.assertRaisesRegex(RuntimeError, r'before its bridge was ready:.*code 127'):
            self.server.run(simulator)
        self.server.serve.assert_not_called()

    def test_injected_files_preserve_executability_and_use_privileged_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'guest_bridge'
            source.write_bytes(b'executable')
            source.chmod(0o755)
            script = self.module.guest_start_script('/start', [f'/opt/guest_bridge={source}'])
            self.assertIn('chimaera_root tee /opt/guest_bridge >/dev/null', script)
            self.assertIn('chimaera_root chmod 755 /opt/guest_bridge', script)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)

    def test_after_boot_hypercall_is_a_boot_boundary(self):
        simulator = self.simulator([('m5_hypercall instruction encountered', 10),
                                    (Event.WORKBEGIN, 10)])
        simulator.get_hypercall_id = Mock(return_value=3)
        self.server.serve = Mock()
        self.server.run(simulator)
        self.assertTrue(self.server.roi_started)
        self.server.serve.assert_called_once()

    def test_other_hypercalls_are_not_treated_as_boot_boundaries(self):
        simulator = self.simulator([('m5_hypercall instruction encountered', 10)])
        simulator.get_hypercall_id = Mock(return_value=7)
        self.server.serve = Mock()
        with self.assertRaisesRegex(NotImplementedError, 'not implemented'):
            self.server.run(simulator)
        self.server.serve.assert_not_called()

    def test_socket_protocol_errors_steps_quit_and_owned_path_cleanup(self):
        self.server.roi_started = True
        self.simulator([(Event.MAX_TICK, 100)])
        connections = []
        for line in ('RESET', 'STATUS', 'STEP_TICKS 100', 'QUIT'):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.recv.side_effect = [bytes([byte]) for byte in (line + '\n').encode()]
            connections.append(connection)
        listener = MagicMock()
        listener.__enter__.return_value = listener
        listener.accept.side_effect = [(connection, None) for connection in connections]
        path = Mock()
        identity = Mock(st_dev=1, st_ino=2)
        path.lstat.return_value = identity
        with patch.object(self.module.socket, 'socket', return_value=listener), \
             patch.object(self.module, 'Path', return_value=path), \
             patch.object(self.module.signal, 'signal', return_value='previous') as signals:
            self.server.serve('/tmp/test.sock', managed_shutdown=True)
        responses = [connection.sendall.call_args.args[0] for connection in connections]
        self.assertTrue(responses[0].startswith(b'ERROR expected STEP_US'))
        self.assertEqual(responses[1:], [b'PAUSED 0\n', b'OK 0 100\n', b'BYE\n'])
        path.unlink.assert_called_once_with()
        self.m5.stats.dump.assert_called_once_with()
        self.assertEqual(signals.call_args_list[-1].args,
                         (self.module.signal.SIGINT, 'previous'))

    def test_guest_startup_as_root_does_not_require_sudo(self):
        # Docker guests boot as root with no sudo package. Force that branch
        # even when the regression suite itself runs as an ordinary user.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'input', root / 'injected'
            source.write_text('controller settings\n')
            source.chmod(0o755)
            command = root / 'start'
            command.write_text('#!/bin/bash\ncat "$1"\n')
            command.chmod(0o755)
            script = self.module.guest_start_script(
                str(command), [f'{destination}={source}'])
            script = script.replace('(( EUID == 0 ))', 'true')
            script = script.replace('sudo -n "$@"', 'exit 99')
            # Verify injection, modes, and workload execution together.
            command.write_text(f'#!/bin/bash\ncat "{destination}"\n')
            result = subprocess.run(['bash'], input=script, text=True,
                                    capture_output=True, check=True)
            self.assertEqual(result.stdout, source.read_text())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)

    def test_guest_startup_files_and_shell_quoting(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'controller settings.yaml'
            source.write_text('speed: 0.35\n')
            script = self.module.guest_start_script(
                "/opt/robot's files/start", [f"/tmp/robot's settings.yaml={source}"],
                preamble='echo experiment')
            self.assertIn('c3BlZWQ6IDAuMzUK', script)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            for target in ('relative.yaml', '/', '/tmp/../etc/file'):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    self.module.guest_start_script('/start', [target + '=' + str(source)])
        parser = argparse.ArgumentParser()
        self.module.add_chimaera_arguments(parser, guest_command='/opt/custom/start')
        self.assertEqual(parser.parse_args([]).guest_command, '/opt/custom/start')


if __name__ == '__main__':
    unittest.main()
