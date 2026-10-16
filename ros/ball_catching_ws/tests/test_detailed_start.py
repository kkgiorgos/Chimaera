"""Exercise the experiment lifecycle without booting a guest."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/gem5_ball_catching.py'


def lifecycle(tmp_path):
    calls = []
    boot = tmp_path / 'readfile'
    boot.write_text('boot script')
    (tmp_path / 'board.pc.com_1.device').write_text('CHIMAERA_ONLINE_CPUS=4\n')
    core = SimpleNamespace(get_type=lambda: SimpleNamespace(value='o3'),
                           get_simobject=lambda: SimpleNamespace(type='X86O3CPU'))
    processor = SimpleNamespace(switch=lambda: calls.append('switch'), get_cores=lambda: [core] * 4)
    m5 = SimpleNamespace(curTick=lambda: 12000, options=SimpleNamespace(outdir=tmp_path),
                         stats=SimpleNamespace(reset=lambda: calls.append('reset')))
    namespace = dict(Path=Path, json=json, re=__import__('re'), m5=m5, processor=processor,
                     board=SimpleNamespace(readfile=boot), hardware={'num_cores': 4}, cpu_type='o3',
                     TimingServer=type('TimingServer', (), {'roi_started': True, 'finished': False}))
    tree = ast.parse(SCRIPT.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SCRIPT), 'exec'), namespace)
    return namespace, calls


def test_readiness_clock_starts_without_switching_and_survives_throw(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['prepare_clock']()
    origin = (tmp_path / 'roi_time_origin.json').read_bytes()
    assert calls == []
    assert not (tmp_path / 'cpu_roi.json').exists()
    assert not (tmp_path / 'measurement_start.json').exists()
    server = scope['BallTimingServer']()
    assert server.handle_command('BENCHMARK_BEGIN') == 'READY 12000'
    assert calls == ['switch', 'reset']
    assert json.loads((tmp_path / 'measurement_start.json').read_text())['tick'] == 12000
    assert json.loads((tmp_path / 'cpu_roi.json').read_text())['simobjects'] == ['X86O3CPU'] * 4
    assert (tmp_path / 'roi_time_origin.json').read_bytes() == origin
    with pytest.raises(RuntimeError, match='already started'):
        server.handle_command('BENCHMARK_BEGIN')
    assert calls == ['switch', 'reset']


def test_failed_cpu_switch_does_not_start_measurement(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['processor'].get_cores = lambda: []
    with pytest.raises(RuntimeError, match='requested model'):
        scope['BallTimingServer']().begin_throw()
    assert calls == ['switch']
    assert not (tmp_path / 'measurement_start.json').exists()


def test_switch_drain_is_charged_to_next_host_interval(tmp_path):
    scope, calls = lifecycle(tmp_path)
    def switch():
        scope['m5'].curTick = lambda: 13000
    scope['processor'].switch = switch
    def run_for_ticks(self, ticks):
        calls.append(ticks)
        start = scope['m5'].curTick()
        scope['m5'].curTick = lambda: start + ticks
        return f'OK {start} {start + ticks}'
    scope['TimingServer'].run_for_ticks = run_for_ticks
    server = scope['BallTimingServer']()
    assert server.begin_throw() == 'READY 13000'
    assert server.run_for_ticks(5000) == 'OK 12000 17000'
    assert calls == ['reset', 4000]
    assert server.run_for_ticks(5000) == 'OK 17000 22000'
    assert calls[-1] == 5000
