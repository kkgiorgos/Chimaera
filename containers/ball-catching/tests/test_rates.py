"""The setup gate rejects quantized pressure levels using actual statistics."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'ros/ball_catching_ws/scripts'))
import check_aggressor_rates as rates


def fixture(tmp_path):
    binary = tmp_path / 'gem5/build/X86/gem5.opt'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'test simulator')
    calibration = tmp_path / 'calibration.json'
    calibration.write_text(json.dumps(dict(pattern='stream', sustainable_gbps=51.2,
        gem5_sha256=rates.digest(binary))))
    config = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_demo.json'
    return config, calibration, tmp_path / 'gem5'


@pytest.mark.parametrize('quantized', [False, True])
def test_rate_gate_accepts_requested_rates_and_rejects_old_quantization(tmp_path, monkeypatch, quantized):
    config, calibration, gem5 = fixture(tmp_path)
    def simulate(command, **kwargs):
        output = Path(next(a.split('=', 1)[1] for a in command if a.startswith('--outdir=')))
        offered = float(command[command.index('--offered-gbps') + 1])
        achieved = 34.133333 if quantized else offered
        (output / 'stats.txt').write_text('simTicks 1000000000\nsimFreq 1000000000000\n'
            f'system.memory.aggressorBytes {round(achieved * 1e6)}\n')
    monkeypatch.setattr(rates.subprocess, 'run', simulate)
    if quantized:
        with pytest.raises(ValueError, match='Incorrect aggressor pacing'):
            rates.check_rates(config, calibration, gem5, tmp_path / 'out')
    else:
        result = rates.check_rates(config, calibration, gem5, tmp_path / 'out')
        assert result['passed']
        assert [r['pressure'] for r in result['rates']] == [.5, .8, .9]
        assert [r['accepted_gbps'] for r in result['rates']] == pytest.approx([25.6,40.96,46.08])
        assert json.loads((tmp_path / 'out/validation.json').read_text()) == result
