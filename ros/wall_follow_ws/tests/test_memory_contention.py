"""Contention designs hold hardware fixed and distinguish memory from task time."""
import json
from pathlib import Path
import sys

import pytest

from wall_follow_benchmark.analysis import summarize
from wall_follow_benchmark.hardware import DEFAULTS, validate
from wall_follow_benchmark.gem5_stats import summarize as summarize_stats

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / 'scripts'))
from run_experiments import make_plan


def test_contention_design_changes_only_device_traffic():
    config = json.loads((WORKSPACE / 'experiments/memory_contention.json').read_text())
    plan = make_plan(config, 'shared-ddr4', gem5=True)
    assert len(plan['runs']) == 16
    hardware = [{key: value for key, value in run['parameters'].items()
                 if not key.startswith('aggressor_')} for run in plan['runs']]
    assert all(value == hardware[0] for value in hardware)
    assert plan['runs'][0]['parameters']['aggressor_pattern'] == 'none'
    with pytest.raises(ValueError, match='Unknown launch parameter'):
        make_plan(config, 'native', gem5=False)


@pytest.mark.parametrize('changes', [
    dict(aggressor_pattern='stream'),
    dict(memory_backend='ramulator2'),
    dict(aggressor_interval=0), dict(aggressor_stride=65),
    dict(aggressor_read_percent=101), dict(aggressor_duty_percent=-1),
    dict(aggressor_seed=0), dict(aggressor_max_pending=2**32),
])
def test_invalid_device_configuration(changes):
    with pytest.raises(ValueError):
        validate(dict(DEFAULTS, **changes))


def test_command_gaps_use_simulated_time_and_ignore_duplicate_receipts():
    rows = [dict(elapsed=t, wall_elapsed=100*t, path_m=t, gt_error=0,
                 control_hz=20, host_scan_age=.01)
            for t in (0, .05, .05, .10, .20)]
    result = summarize(rows)
    assert result['command_gap_max_ms'] == pytest.approx(100)
    assert result['command_late_fraction'] == pytest.approx(1/3)
    assert result['host_scan_age_p95_ms'] == pytest.approx(10)
    assert summarize(rows, warmup=.21)['command_gap_p95_ms'] is None


def test_dram_metrics_use_tick_frequency_and_roi_seconds(tmp_path):
    stats = tmp_path / 'stats.txt'
    stats.write_text('''---------- Begin Simulation Statistics ----------
simSeconds 2
simFreq 1000000000000
board.memory.mem_ctrl.aggressorBytes 8000000000
board.memory.mem_ctrl.aggressorAccepted 100
board.memory.mem_ctrl.aggressorRejected 25
board.memory.mem_ctrl.cpuReads 10
board.memory.mem_ctrl.cpuReadLatencyTicks 500000
board.memory.mem_ctrl.cpuRetries 3
---------- End Simulation Statistics ----------
''')
    result = summarize_stats(stats)
    assert result['aggressor_gbps'] == 4
    assert result['cpu_dram_read_ns'] == 50
    assert result['aggressor_blocked_fraction'] == .2
    assert result['cpu_dram_retries'] == 3


def test_container_custom_export_is_immutable_and_relative(tmp_path):
    import importlib.util
    path = WORKSPACE.parents[1] / 'containers/wall-follow/configure.py'
    spec = importlib.util.spec_from_file_location('contention_container_config', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = b'{"frontend":{"impl":"External"},"memory_system":{}}'
    (tmp_path / 'memory.json').write_bytes(original)
    config = dict(fixed=dict(ramulator_config='memory.json'))
    frozen, files = module.memory_inputs(config, tmp_path / 'experiment.json')
    assert config['fixed']['ramulator_config'] == 'memory.json'
    assert frozen['fixed']['ramulator_config'].startswith('/assets/memory-configs/')
    assert list(files.values()) == [original]
    (tmp_path / 'memory.json').write_text('{}')
    assert list(files.values()) == [original]
    with pytest.raises(ValueError, match='External'):
        module.memory_inputs(config, tmp_path / 'experiment.json')


def test_first_command_preserves_startup_before_collection_and_warmup():
    rows = [dict(elapsed=t, sim_time=t+.15, wall_elapsed=100*t, path_m=t,
                 gt_error=0, control_hz=20, host_scan_age=.01)
            for t in (0, .05, .10, .15)]
    assert summarize(rows, warmup=.10)['first_command_sim_s'] == .15
