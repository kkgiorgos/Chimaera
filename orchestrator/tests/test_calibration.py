import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ros/ball_catching_ws/scripts'))
from run_memory_suite import calibrate, digest


def fixture(tmp_path):
    simulator = tmp_path / 'gem5'
    simulator.write_bytes(b'frozen simulator')
    data = dict(patterns=['stream'], ramulator_sha256='memory-hash', aggressor={}, calibration={})
    signature = hashlib.sha256(json.dumps({key: data[key] for key in
        ('ramulator_sha256', 'aggressor', 'calibration')}, sort_keys=True).encode()).hexdigest()
    record = dict(signature=signature, gem5_sha256=digest(simulator), pattern='stream', sustainable_gbps=40)
    source = tmp_path / 'shared/stream'
    source.mkdir(parents=True)
    (source / 'calibration.json').write_text(json.dumps(record))
    return simulator, data, record, source.parent


def test_shared_calibration_reuses_frozen_bandwidth_without_simulating(tmp_path):
    simulator, data, record, source = fixture(tmp_path)
    with patch('run_memory_suite.subprocess.run') as execute:
        assert calibrate(tmp_path / 'output', data, simulator, source) == {'stream': record}
        execute.assert_not_called()
    assert json.loads((tmp_path / 'output/calibration/stream/calibration.json').read_text()) == record


def test_different_simulator_rejects_shared_calibration_without_recalibrating(tmp_path):
    simulator, data, record, source = fixture(tmp_path)
    simulator.write_bytes(b'other simulator')
    with patch('run_memory_suite.subprocess.run') as execute:
        with pytest.raises(ValueError, match='provenance differs'):
            calibrate(tmp_path / 'output', data, simulator, source)
        execute.assert_not_called()
