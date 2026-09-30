"""Exercise the actual clock watchdog methods without starting ROS or gem5."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from wall_follow_benchmark.configuration import DEFAULTS

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / 'src/wall_follow_benchmark'))
from wall_follow_host.recording import Recorder


def collector_methods():
    source = WORKSPACE / 'src/wall_follow_benchmark/wall_follow_host/collector.py'
    module = ast.parse(source.read_text())
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'Collector')
    # Keep the production methods, replacing only the ROS Node base and constructor.
    cls.bases = []
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                and node.name in ('on_clock', 'finish')]
    stamp = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'stamp_seconds')
    namespace = dict(rclpy=SimpleNamespace(ok=lambda: False))
    exec(compile(ast.Module(body=[stamp, cls], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['Collector']


def clock(seconds):
    nanoseconds = round(seconds * 1e9)
    return SimpleNamespace(clock=SimpleNamespace(sec=nanoseconds // 10**9,
                                                nanosec=nanoseconds % 10**9))


def test_simulated_command_gap_reports_exact_failure_context(tmp_path):
    collector = collector_methods()()
    collector.finished = collector.success = False
    collector.sim = collector.last_command = .55
    collector.command_timeout = 1.
    collector.duration = 3.
    collector.recorder = Recorder(tmp_path, DEFAULTS, 3.)
    collector.recorder.record_command(.55, 100., .245, -.36)
    messages = []
    collector.get_logger = lambda: SimpleNamespace(info=messages.append, error=messages.append)
    collector.on_clock(clock(1.55))
    assert not collector.finished  # Strictly greater than one simulated second.
    collector.on_clock(clock(1.6))
    metadata = json.loads((tmp_path / 'metadata.json').read_text())
    assert collector.finished and not collector.success
    assert metadata['finish_reason'] == 'robot command timeout'
    assert metadata['sample_count'] == 1 and not metadata['completed']
    assert metadata['collection_status'] == pytest.approx(dict(
        finish_sim_time=1.6, last_command_sim_time=.55,
        command_gap_sim_seconds=1.05, command_timeout_sim_seconds=1.))
    assert len(messages) == 1
    assert 'gap 1.050s (timeout 1.000s)' in messages[0]
    assert '1 command receipts' in messages[0]
    collector.finish(True, 'duration reached')
    assert json.loads((tmp_path / 'metadata.json').read_text()) == metadata
