"""Exercise gem5 event handlers without booting a full-system image."""
import ast
from pathlib import Path
from types import SimpleNamespace


def test_workbegin_switches_once_and_quit_dumps_roi():
    config = Path(__file__).resolve().parents[1] / 'src/wall_follow_bridge/config/gem5_wall_follow.py'
    tree = ast.parse(config.read_text())
    handlers = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                               and node.name in ('on_workbegin', 'handle_command')], type_ignores=[])
    calls = []
    namespace = dict(roi_started=False, finished=False, exit_requested=False,
                     processor=SimpleNamespace(switch=lambda: calls.append('switch')),
                     m5=SimpleNamespace(stats=SimpleNamespace(
                         reset=lambda: calls.append('reset'), dump=lambda: calls.append('dump'))))
    exec(compile(handlers, str(config), 'exec'), namespace)
    handler = namespace['on_workbegin']()
    assert next(handler) is True
    assert calls == ['switch', 'reset']
    assert namespace['roi_started'] is True
    # SimpleSwitchableProcessor.switch toggles; a second marker must not go back
    # to KVM or discard ROI statistics.
    assert next(handler) is True
    assert calls == ['switch', 'reset']
    assert namespace['handle_command']('QUIT') == 'BYE'
    assert calls == ['switch', 'reset', 'dump']
    assert namespace['exit_requested'] is True
