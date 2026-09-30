"""Exercise gem5 event handlers without booting a full-system image."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


def test_workbegin_switches_once_and_quit_dumps_roi():
    config = Path(__file__).resolve().parents[1] / 'src/wall_follow_bridge/config/gem5_wall_follow.py'
    tree = ast.parse(config.read_text())
    handlers = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                               and node.name in ('on_workbegin', 'handle_command')], type_ignores=[])
    calls = []
    namespace = dict(roi_started=False, finished=False, exit_requested=False,
                     verify_online_cpus=lambda: calls.append('verify'),
                     processor=SimpleNamespace(switch=lambda: calls.append('switch')),
                     m5=SimpleNamespace(stats=SimpleNamespace(
                         reset=lambda: calls.append('reset'), dump=lambda: calls.append('dump'))))
    exec(compile(handlers, str(config), 'exec'), namespace)
    handler = namespace['on_workbegin']()
    assert next(handler) is True
    assert calls == ['verify', 'switch', 'reset']
    assert namespace['roi_started'] is True
    # SimpleSwitchableProcessor.switch toggles; a second marker must not go back
    # to KVM or discard ROI statistics.
    assert next(handler) is True
    assert calls == ['verify', 'switch', 'reset']
    assert namespace['handle_command']('QUIT') == 'BYE'
    assert calls == ['verify', 'switch', 'reset', 'dump']
    assert namespace['exit_requested'] is True


@pytest.mark.parametrize('serial_text, expected, error', [
    ('boot log\nCHIMAERA_ONLINE_CPUS=2\n', 2, None),
    ('boot log\r\nCHIMAERA_ONLINE_CPUS=1\r\n', 1, None),
    ('CHIMAERA_ONLINE_CPUS=1\n', 2, 'Guest has 1 online CPUs; requested 2'),
    ('smp: Brought up 1 node, 1 CPU\n', 2, 'Guest online CPU count missing'),
    ('CHIMAERA_ONLINE_CPUS=2\nCHIMAERA_ONLINE_CPUS=1\n', 2,
     'Guest has 1 online CPUs; requested 2'),
])
def test_online_cpu_verification(tmp_path, serial_text, expected, error):
    config = Path(__file__).resolve().parents[1] / 'src/wall_follow_bridge/config/gem5_wall_follow.py'
    tree = ast.parse(config.read_text())
    functions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                and node.name in ('verify_online_cpus', 'on_workbegin')],
                           type_ignores=[])
    (tmp_path/'board.pc.com_1.device').write_text(serial_text)
    calls = []
    namespace = dict(Path=Path, re=re, args=SimpleNamespace(num_cores=expected),
                     roi_started=False,
                     processor=SimpleNamespace(switch=lambda: calls.append('switch')),
                     m5=SimpleNamespace(options=SimpleNamespace(outdir=str(tmp_path)),
                                        stats=SimpleNamespace(reset=lambda: calls.append('reset'))))
    exec(compile(functions, str(config), 'exec'), namespace)
    handler = namespace['on_workbegin']()
    if error:
        with pytest.raises(RuntimeError, match=re.escape(error)):
            next(handler)
        assert not calls
        assert namespace['roi_started'] is False
    else:
        assert next(handler) is True
        assert calls == ['switch', 'reset']
