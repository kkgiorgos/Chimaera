"""Operator wrapper checks with Docker and the benchmark runner fully mocked."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import PERFORMANCE_VARIANTS, VARIANTS, read_json, write_json
from matrix import make_matrix
import run_isolated


@contextmanager
def fixture():
    tooling = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(dir=tooling) as folder:
        local = Path(folder)
        args = SimpleNamespace(root=tooling.parents[1], matrix=local / 'matrix.json',
            lock=local / 'lock.json', gate=local / 'gate.json', output=local / 'suite',
            middleware_setup=local / 'middleware/setup.bash', runtime_setup=local / 'runtime/setup.bash',
            cases=None, operator_timeout=1)
        write_json(args.matrix, dict(cases=[dict(name='one'), dict(name='two')]))
        write_json(args.lock, dict(environment=dict(sdk_image='sha256:' + 'a' * 64), tooling_files={}))
        args.gate.write_text('{}')
        with patch.object(run_isolated, 'tree_hashes', return_value={}):
            yield args


def completed_case(args, command, marker=True):
    container_command = command[command.index('/work/' + str(Path(run_isolated.__file__).resolve().parent.relative_to(args.root)) + '/container_case.sh') + 1:]
    case = container_command[2]
    write_json(args.output / case / 'benchmark-attempt.json', dict(status='complete'))
    if marker:
        container_path = Path(command[command.index('--completion-record') + 1])
        host_path = args.root / container_path.relative_to('/work')
        write_json(host_path, dict(operator_token=command[command.index('--operator-token') + 1],
            case=case, runner_return_code=1))
    return SimpleNamespace(returncode=1)


class IsolationTests(unittest.TestCase):
    def test_resume_keeps_operator_failure_even_after_case_wrote_complete(self):
        with fixture() as args, patch.object(run_isolated.subprocess, 'run') as docker:
            args.cases = ['one']
            write_json(args.output / 'one/benchmark-attempt.json', dict(status='complete'))
            write_json(args.output.parent / (args.output.name + '-operator') / 'old/one.json',
                       dict(status='timeout', success=False))
            self.assertEqual(run_isolated.run_cases(args), 1)
            docker.assert_not_called()

    def test_existing_failure_and_incomplete_attempt_are_retained_as_failures(self):
        with fixture() as args, patch.object(run_isolated.subprocess, 'run') as docker:
            write_json(args.output / 'one/benchmark-attempt.json', dict(status='failed', error='raw failure'))
            (args.output / 'two').mkdir()
            self.assertEqual(run_isolated.run_cases(args), 1)
            self.assertEqual(read_json(args.output / 'one/benchmark-attempt.json')['error'], 'raw failure')
            docker.assert_not_called()

    def test_fresh_private_containers_use_locked_sdk_and_accept_normal_partial_exit(self):
        with fixture() as args:
            calls = []
            def execute(command, **kwargs):
                calls.append(command)
                return completed_case(args, command)
            with patch.object(run_isolated.subprocess, 'run', side_effect=execute):
                self.assertEqual(run_isolated.run_cases(args), 0)
            containers = [cmd[cmd.index('--name') + 1] for cmd in calls]
            self.assertEqual(len(set(containers)), 2)
            for command in calls:
                self.assertIn('sha256:' + 'a' * 64, command)
                self.assertEqual(command[command.index('--cpus') + 1], '4')
                self.assertEqual(command[command.index('--memory') + 1], '24g')
                self.assertEqual(command[command.index('--shm-size') + 1], '2g')
                self.assertIn('--rm', command)
                self.assertFalse(any('/tmp:' in argument for argument in command))
                self.assertIn('--lock', command)
                self.assertIn('--gate', command)

    def test_uncaught_runner_error_cannot_pass_with_a_complete_case_record(self):
        with fixture() as args, patch.object(run_isolated.subprocess, 'run',
                side_effect=lambda command, **kwargs: completed_case(args, command, marker=False)):
            self.assertEqual(run_isolated.run_cases(args), 1)

    def test_explicit_case_order_and_resume_fingerprint_are_preserved(self):
        with fixture() as args:
            args.cases = ['two', 'one']
            calls = []
            def execute(command, **kwargs):
                calls.append(command)
                return completed_case(args, command)
            with patch.object(run_isolated.subprocess, 'run', side_effect=execute):
                self.assertEqual(run_isolated.run_cases(args), 0)
            self.assertEqual([cmd[cmd.index('--operator-token') - 1].endswith('-runner.json') for cmd in calls], [True, True])
            results = list(args.output.parent.glob('suite-operator/*/result.json'))
            self.assertEqual(read_json(results[0])['selected_cases'], ['two', 'one'])
            write_json(args.output / 'suite.json', dict(input_sha256=dict(matrix='foreign')))
            with patch.object(run_isolated.subprocess, 'run') as docker, self.assertRaisesRegex(ValueError, 'identical frozen'):
                run_isolated.run_cases(args)
            docker.assert_not_called()

    def test_timeout_and_cancel_stop_only_label_verified_owned_container(self):
        for error, expected in ((subprocess.TimeoutExpired('docker', 1), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=type(error).__name__), fixture() as args:
                args.cases = ['one']
                calls, token, owned_name = [], None, None
                def execute(command, **kwargs):
                    nonlocal token, owned_name
                    calls.append(command)
                    if command[1] == 'run':
                        token = command[command.index('--label') + 1].split('=', 1)[1]
                        owned_name = command[command.index('--name') + 1]
                        raise error
                    if command[1] == 'inspect':
                        self.assertEqual(command[-1], owned_name)
                        return SimpleNamespace(returncode=0, stdout=token + '\n')
                    self.assertEqual(command, ['docker', 'stop', '--timeout', '5', owned_name])
                    return SimpleNamespace(returncode=0)
                with patch.object(run_isolated.subprocess, 'run', side_effect=execute):
                    self.assertEqual(run_isolated.run_cases(args), expected)
                self.assertEqual([command[1] for command in calls], ['run', 'inspect', 'stop'])

    def test_cleanup_refuses_another_container_owner(self):
        with patch.object(run_isolated.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0, stdout='someone-else\n')) as docker:
            run_isolated.stop_owned('name', 'our-token', None)
            self.assertEqual(docker.call_count, 1)

    def test_shell_completion_marker_requires_a_normal_runner_return(self):
        source = Path(run_isolated.__file__).with_name('container_case.sh').read_text()
        bootstrap = source.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            runner, marker = Path(folder) / 'run.py', Path(folder) / 'marker.json'
            runner.write_text('raise SystemExit(1)\n')
            command = [sys.executable, '-', str(runner), str(marker), 'token', 'one', 'matrix', '--cases', 'one']
            result = subprocess.run(command, input=bootstrap, text=True, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(read_json(marker), dict(operator_token='token', case='one', runner_return_code=1))
            marker.unlink()
            runner.write_text('raise RuntimeError("frozen integrity failed")\n')
            result = subprocess.run(command, input=bootstrap, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())


class MatrixIdentityTests(unittest.TestCase):
    def arguments(self, **overrides):
        values = dict(stage='all-prior', variants=None, repeats=1, duration=1., policies=['lossless'],
                      bulk_bytes=None, cells=None, correctness=False)
        return SimpleNamespace(**dict(values, **overrides))

    def test_default_main_cohort_uses_ready_and_defers_final_diagnostic(self):
        matrix = make_matrix(self.arguments())
        self.assertEqual(len(VARIANTS), 9)
        self.assertEqual(matrix['selected_implementations'], list(PERFORMANCE_VARIANTS))
        self.assertIn('initialized_m5_ready', matrix['selected_implementations'])
        self.assertEqual(matrix['deferred_implementations'], ['initialized_m5'])
        self.assertTrue(all(case['comparison_role'] == 'main' for case in matrix['cases']))

    def test_final_pair_can_be_selected_only_as_explicit_diagnostic(self):
        matrix = make_matrix(self.arguments(variants=['initialized_m5']))
        self.assertTrue(all(case['implementation'] == 'initialized_m5' and
                            case['comparison_role'] == 'diagnostic' for case in matrix['cases']))


if __name__ == '__main__':
    unittest.main()
