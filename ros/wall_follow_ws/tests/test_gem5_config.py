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
    ('CHIMAERA_ONLINE_CPU[   96.017954] random: python3: uninitialized urandom read (24 bytes read)\nS=2\n', 2, None),
    ('CHIMAE[    2.636117] random: python3: uninitialized urandom read (24 bytes read)\nRA_ONLINE_CPUS=2\n', 2, None),
    ('CHIMAERA_ONLINE_CPUS=[    2.636117] random: warning\n1\n', 2,
     'Guest has 1 online CPUs; requested 2'),
    ('CHIMAERA_ONLINE_CPUS=invalid\n', 2, 'Guest online CPU count missing'),
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


@pytest.mark.parametrize('split', range(len('CHIMAERA_ONLINE_CPUS=12') + 1))
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_kernel_output_at_every_cpu_marker_boundary(tmp_path, split, newline):
    marker = 'CHIMAERA_ONLINE_CPUS=12'
    warning = '[    2.636117] random: python3: uninitialized urandom read' + newline
    serial = 'boot log' + newline + marker[:split] + warning + marker[split:] + newline
    test_online_cpu_verification(tmp_path, serial, 12, None)
