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
    processor = SimpleNamespace(switch_to_processor=lambda key: calls.append(('switch', key)),
                                get_cores=lambda: [core] * 4)
    m5 = SimpleNamespace(curTick=lambda: 12000, options=SimpleNamespace(outdir=tmp_path),
                         stats=SimpleNamespace(reset=lambda: calls.append('reset')))
    namespace = dict(Path=Path, json=json, re=__import__('re'), m5=m5, processor=processor,
                     board=SimpleNamespace(readfile=boot), hardware={'num_cores': 4}, cpu_type='o3',
                     restore=None, capture=None,
                     memory=SimpleNamespace(get_memory_controllers=lambda: [SimpleNamespace(
                         setAggressorEnabled=lambda enabled: calls.append(('pressure', enabled)))]),
                     TimingServer=type('TimingServer', (), {'roi_started': True, 'finished': False}))
    tree = ast.parse(SCRIPT.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SCRIPT), 'exec'), namespace)
    return namespace, calls


def test_detailed_readiness_has_no_aggressor_and_throw_does_not_switch(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['prepare_clock']()
    origin = (tmp_path / 'roi_time_origin.json').read_bytes()
    assert calls == [('switch', 'detailed')]
    assert (tmp_path / 'cpu_roi.json').exists()
    assert not (tmp_path / 'measurement_start.json').exists()
    server = scope['BallTimingServer']()
    assert server.handle_command('BENCHMARK_BEGIN') == 'READY 12000'
    assert calls == [('switch', 'detailed'), ('pressure', True), 'reset']
    assert json.loads((tmp_path / 'measurement_start.json').read_text())['tick'] == 12000
    assert json.loads((tmp_path / 'cpu_roi.json').read_text())['simobjects'] == ['X86O3CPU'] * 4
    assert (tmp_path / 'roi_time_origin.json').read_bytes() == origin
    with pytest.raises(RuntimeError, match='already started'):
        server.handle_command('BENCHMARK_BEGIN')
    assert calls == [('switch', 'detailed'), ('pressure', True), 'reset']


def test_failed_cpu_switch_does_not_start_measurement(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['processor'].get_cores = lambda: []
    with pytest.raises(RuntimeError, match='requested model'):
        scope['BallTimingServer']().begin_throw()
    assert calls == []
    assert not (tmp_path / 'measurement_start.json').exists()


def test_checkpoint_quiescence_is_charged_to_first_restored_interval(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['restore'] = '/checkpoint'
    scope['m5'].curTick = lambda: 13000
    def run_for_ticks(self, ticks):
        calls.append(ticks)
        start = scope['m5'].curTick()
        scope['m5'].curTick = lambda: start + ticks
        return f'OK {start} {start + ticks}'
    scope['TimingServer'].run_for_ticks = run_for_ticks
    server = scope['BallTimingServer']()
    server.restore_boundary_tick = 12000
    server.pressure_started = True
    assert server.begin_throw() == 'READY 13000'
    assert server.run_for_ticks(5000) == 'OK 12000 17000'
    assert calls == ['reset', 4000]
    assert server.run_for_ticks(5000) == 'OK 17000 22000'
    assert calls[-1] == 5000


def test_restore_reports_clock_gap_before_pressure_or_first_interval(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['restore'] = '/checkpoint'
    scope['m5'].curTick = lambda: 17000
    server = scope['BallTimingServer']()
    server.restore_boundary_tick = 12000
    assert server.handle_command('BENCHMARK_RESTORE') == 'RESTORE 12000 17000'
    assert calls == []
    server.pressure_started = True
    with pytest.raises(RuntimeError, match='No pending restored host boundary'):
        server.handle_command('BENCHMARK_RESTORE')
    server.pressure_started = False
    server.restore_boundary_tick = None
    with pytest.raises(RuntimeError, match='No pending restored host boundary'):
        server.handle_command('BENCHMARK_RESTORE')


def test_restored_pressure_warmup_is_separate_from_measurement(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['restore'] = '/checkpoint'
    server = scope['BallTimingServer']()
    assert server.handle_command('BENCHMARK_PRESSURE') == 'PRESSURE 12000'
    assert calls == [('pressure', True)]
    assert not (tmp_path / 'measurement_start.json').exists()
    server.begin_throw()
    assert calls == [('pressure', True), 'reset']


def test_restored_launch_requires_pressure_and_does_not_restart_it(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['restore'] = '/checkpoint'
    server = scope['BallTimingServer']()
    with pytest.raises(RuntimeError, match='requires the pressure warmup'):
        server.begin_throw()
    server.begin_pressure()
    with pytest.raises(RuntimeError, match='enable pressure'):
        server.begin_pressure()
    assert calls == [('pressure', True)]


def test_checkpoint_preparation_cannot_enable_pressure_or_throw(tmp_path):
    scope, calls = lifecycle(tmp_path)
    scope['capture'] = '/checkpoint'
    server = scope['BallTimingServer']()
    with pytest.raises(RuntimeError, match='enable pressure'):
        server.begin_pressure()
    with pytest.raises(RuntimeError, match='never throw'):
        server.begin_throw()
    assert calls == []
