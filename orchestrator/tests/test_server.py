import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from controller import validate_config
from server import serve


ROOT = Path(__file__).resolve().parents[2]


def test_defaults_endpoint_returns_two_saved_dds_m5ops_lofts():
    with patch('server.ThreadingHTTPServer') as http:
        serve(SimpleNamespace(root=ROOT))
    handler = object.__new__(http.call_args.args[1])
    handler.path = '/defaults'
    handler.respond = Mock()
    handler.do_GET()
    response = handler.respond.call_args.args
    assert len(response) == 1
    config = response[0]['config']
    assert config == json.loads((ROOT / 'orchestrator/experiments/dds_m5ops_two_lofts.json').read_text())
    assert config['fixed']['transport_backend'] == 'm5ops'
    assert config['fixed']['middleware_transport'] == 'fastdds'
    assert not config['fixed']['m5_module'] and not config['fixed']['dma_module']
    assert config['ramulator_config'] == 'gem5/ext/ramulator2/configs/lpddr5-6400-orin.json'
    _, cases = validate_config(ROOT, config)
    assert len(cases) == 2
    assert all(case['pressure'] == 0 and case['pattern'] == 'none' for case in cases)
    assert [case['throw'] for case in cases] == [
        dict(name='loft-3', launch_position=[1.8, .04, 3], launch_direction=[-1, 0, 2], launch_speed=3),
        dict(name='loft-5', launch_position=[4.36, .04, 3], launch_direction=[-1, 0, 1], launch_speed=5),
    ]
