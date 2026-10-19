import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_memory_suite as runner
from test_experiment_report import write_trial
from test_guest_records import envelope, write_capture
from test_memory_benchmark import write_memory


def saved_suite(directory):
    trial = directory / 'case-007-attempt-001'
    trial.mkdir()
    write_trial(trial, success=False)
    # Retain only the raw capture, just as the former importer did on failure.
    for name in ('perception', 'interception', 'control_timing'):
        (trial / (name + '.jsonl')).unlink()
    perception = envelope('perception')
    perception['record'].update(capture_time=1., receive_time=1.001, state=None)
    write_capture(trial, [envelope('control_timing'), perception,
                          envelope('control_timing', 2)])
    (trial / 'commands.csv').write_text('receipt_time,command_time\n1.01,1\n')
    (trial / 'launch_validation.json').write_text('{"valid":true}')
    (trial / 'ramulator.json').write_text(json.dumps(dict(
        memory_system=dict(controllers=[dict(dram=dict(impl='LPDDR5'))]))))
    (trial / 'execution.json').write_text(json.dumps(dict(
        hardware=dict(cpu_type='timing'), aggressor=dict(pattern='none', gbps=0), fixed={})))
    (trial / 'gem5').mkdir()
    write_memory(trial / 'gem5')
    point = dict(id='case-007', label='Baseline', pattern='none', pressure=0,
                 rates=dict(control_hz=250), status='infrastructure_failure', trials=[],
                 reason='Guest telemetry sequence gap in control_timing: expected 1, received 2')
    manifest = dict(mode='gem5', configuration=dict(peak_gbps=204.8,
                    ramulator_sha256='frozen-memory'), points=[point],
                    provenance=dict(git_revision='original-simulation'))
    (directory / 'experiment_manifest.json').write_text(json.dumps(manifest))
    return trial, manifest


def test_collect_only_recovers_data_and_report_without_simulation(tmp_path, monkeypatch):
    trial, original = saved_suite(tmp_path)
    raw = {str(p.relative_to(tmp_path)): p.read_bytes()
           for p in trial.rglob('*') if p.is_file()}
    monkeypatch.setattr(runner, 'check_local_transport', lambda: pytest.fail('No transport needed'))
    monkeypatch.setattr(runner, 'gem5_trial', lambda *args: pytest.fail('No simulation needed'))
    monkeypatch.setattr(sys, 'argv', ['run_memory_suite.py', '--collect-only',
                                    '--output', str(tmp_path), '--case', 'case-007'])
    assert runner.main() == 0
    for name, expected in raw.items():
        assert (tmp_path / name).read_bytes() == expected
    manifest = json.loads((tmp_path / 'experiment_manifest.json').read_text())
    assert manifest['provenance'] == original['provenance']
    assert manifest['points'][0]['status'] == 'complete'
    assert manifest['points'][0]['trials'] == [trial.name]
    assert manifest['points'][0]['collection_recovery']['original_reason'] == original['points'][0]['reason']
    assert json.loads((tmp_path / 'experiment_manifest.before-collection.json').read_text()) == original
    benchmark = json.loads((trial / 'benchmark.json').read_text())
    assert benchmark['memory']['samples'] == 100
    assert benchmark['telemetry']['status'] == 'partial'
    assert benchmark['telemetry']['sequence_gaps'] == 1
    assert benchmark['wall_seconds'] is None
    assert json.loads((trial / 'summary.json').read_text())['success'] is False
    assert json.loads((trial / 'summary.json').read_text())['timing_coverage']['status'] == 'partial'
    page = (tmp_path / 'dashboard.html').read_text()
    assert 'missing telemetry records' in page
    assert 'outside_cup_at_deadline' in page


@pytest.mark.parametrize('status,reason', [
    ('running', ''), ('infrastructure_failure', 'Attempt exceeded wall time; inspect logs')])
def test_recovery_rejects_active_or_unrelated_failures(tmp_path, status, reason):
    trial, manifest = saved_suite(tmp_path)
    manifest['points'][0].update(status=status, reason=reason)
    (tmp_path / 'experiment_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Only sequence-gap failures'):
        runner.collect_suite(tmp_path, ['case-007'])
    assert not (trial / 'benchmark.json').exists()
    assert json.loads((tmp_path / 'experiment_manifest.json').read_text()) == manifest


def test_recovery_keeps_invalid_launch_failed(tmp_path):
    trial, manifest = saved_suite(tmp_path)
    (trial / 'launch_validation.json').write_text(
        '{"valid":false,"reason":"launch_pose_or_velocity_reset"}')
    with pytest.raises(ValueError, match='Invalid ball launch'):
        runner.collect_suite(tmp_path, ['case-007'])
    assert not (trial / 'benchmark.json').exists()
    assert json.loads((tmp_path / 'experiment_manifest.json').read_text()) == manifest
