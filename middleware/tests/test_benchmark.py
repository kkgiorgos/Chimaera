import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'middleware_benchmark', Path(__file__).resolve().parents[1] / 'benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkLauncherTest(unittest.TestCase):
    def test_generated_cases_keep_ros_timeout_integer_and_dds_controls_matched(self):
        for timeout, expected in (([], 7200), (['--timeout', '123'], 123)):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / 'matrix'
                arguments = ['benchmark.py', '--output', str(output), '--physical-host', 'test']
                for name in ('runtime', 'bundle', 'gem5-root', 'image', 'kernel', 'dma-module'):
                    arguments += ['--' + name, '/unused/' + name]
                arguments += timeout
                with patch.object(sys, 'argv', arguments), \
                        patch.object(benchmark, 'snapshot', return_value=output / 'source.tar.gz'), \
                        patch.object(benchmark.subprocess, 'run',
                                     return_value=subprocess.CompletedProcess([], 1)) as run:
                    self.assertEqual(benchmark.main(), 1)
                cases = json.loads((output / 'cases.json').read_text())
                self.assertEqual(len(cases), 8)
                for case in cases:
                    config = case['config']
                    # ROS declares timeout_s as an integer; 7200.0 aborts startup.
                    self.assertIs(type(config['timeout']), int)
                    self.assertEqual(config['timeout'], expected)
                    self.assertEqual(config['dds_tuning'], 'large_shm_only')
                    self.assertEqual(config['dds_max_message_bytes'], 1048576)
                self.assertTrue(run.call_args.args[0][1].endswith('dds_large_messages.py'))


if __name__ == '__main__':
    unittest.main()
