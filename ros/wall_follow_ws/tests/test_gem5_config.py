"""Exercise gem5 event handlers without booting a full-system image."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


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
                                and node.name in ('verify_online_cpus', 'prepare_roi')],
                           type_ignores=[])
    (tmp_path/'board.pc.com_1.device').write_text(serial_text)
    calls = []
    namespace = dict(Path=Path, re=re, args=SimpleNamespace(num_cores=expected, cpu_type='timing'),
                     processor=SimpleNamespace(switch=lambda: calls.append('switch')),
                     m5=SimpleNamespace(options=SimpleNamespace(outdir=str(tmp_path)),
                                        stats=SimpleNamespace(reset=lambda: calls.append('reset'))))
    exec(compile(functions, str(config), 'exec'), namespace)
    if error:
        with pytest.raises(RuntimeError, match=re.escape(error)):
            namespace['prepare_roi']()
        assert not calls
    else:
        namespace['prepare_roi']()
        assert calls == ['switch']


def test_kvm_roi_does_not_switch_processors():
    config = Path(__file__).resolve().parents[1] / 'src/wall_follow_bridge/config/gem5_wall_follow.py'
    tree = ast.parse(config.read_text())
    functions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                and node.name == 'prepare_roi'], type_ignores=[])
    calls = []
    namespace = dict(args=SimpleNamespace(cpu_type='kvm'),
                     verify_online_cpus=lambda: calls.append('verify'),
                     processor=SimpleNamespace(switch=lambda: calls.append('switch')))
    exec(compile(functions, str(config), 'exec'), namespace)
    namespace['prepare_roi']()
    assert calls == ['verify']
