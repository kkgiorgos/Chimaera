"""Host recording tests run without ROS, including the complete v3 analysis path."""
import csv
import json
from pathlib import Path
import sys

import pytest
from wall_follow_benchmark.configuration import DEFAULTS
from wall_follow_benchmark.comparison import load_run, load_comparison

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/wall_follow_benchmark'))
from wall_follow_host.recording import Recorder


def test_host_recording_keeps_only_measured_data(tmp_path):
    recorder = Recorder(tmp_path, DEFAULTS, 2.)
    for index, sim in enumerate([1., 1., 1.5, 2.]):
        recorder.record_pose(sim, sim, 0., -3.2)
        recorder.record_command(sim, 10.+index*.1, .35, 0., host_scan_stamp=sim-.02)
    recorder.finish(True, 'duration reached')
    run = load_run(tmp_path, 0.)
    assert run['completed']
    assert run['sample_count'] == 4
    assert len(run['series']['elapsed']) == 3
    assert run['metrics']['rmse_m'] == pytest.approx(0., abs=1e-10)
    for key in ('compute_p95_ms', 'scan_age_p95_s', 'stale_fraction',
                'actual_control_hz_sim'):
        assert run['metrics'][key] is None
    assert run['metrics']['host_scan_age_p95_s'] == pytest.approx(.02)
    rows = list(csv.DictReader((tmp_path/'samples.csv').open()))
    assert not any(key.startswith('reference_') for key in rows[0])
    assert not {'compute_ms', 'scan_age', 'state', 'estimated_distance', 'heading', 'front_clearance'} & rows[0].keys()
    assert float(rows[0]['host_scan_stamp']) == pytest.approx(.98)
    assert run['metrics']['command_rate_observed_hz_sim'] == pytest.approx(3.)
    groups, errors = load_comparison([tmp_path], 0.)
    assert not errors and groups[0]['metric_stats']['compute_p95_ms']['n'] == 0
    assert not {'cpu_seconds', 'rss_kib'} & rows[0].keys()
    metadata = json.loads((tmp_path/'metadata.json').read_text())
    assert not {'robot_pid', 'resource_scope'} & metadata.keys()


def test_fresh_output_and_failed_run(tmp_path):
    recorder = Recorder(tmp_path, DEFAULTS, 2.)
    recorder.record_command(1., 1., 0., 0.)
    recorder.finish(False, 'robot command timeout')
    recorder.finish(True)
    assert not json.loads((tmp_path/'metadata.json').read_text())['completed']
    original = (tmp_path/'samples.csv').read_bytes()
    with pytest.raises(FileExistsError):
        Recorder(tmp_path, DEFAULTS, 2.)
    assert (tmp_path/'samples.csv').read_bytes() == original


def test_clock_reset_cannot_silently_enter_an_attempt(tmp_path):
    recorder = Recorder(tmp_path, DEFAULTS, 2.)
    recorder.record_command(2., 1., 0., 0.)
    with pytest.raises(ValueError, match='backwards'):
        recorder.record_command(1., 2., 0., 0.)
    recorder.finish()


def test_host_parameter_events_preserve_sample_targets(tmp_path):
    recorder = Recorder(tmp_path, DEFAULTS, 2.)
    recorder.record_command(1., 1., 0., 0.)
    recorder.update_parameters({'target_distance': 1.}, 1.5)
    recorder.record_command(2., 2., 0., 0.)
    recorder.finish(True)
    rows = list(csv.DictReader((tmp_path/'samples.csv').open()))
    assert [float(row['target_distance']) for row in rows] == [.8, 1.]
    metadata = json.loads((tmp_path/'metadata.json').read_text())
    assert metadata['parameters']['target_distance'] == .8
    assert metadata['final_parameters']['target_distance'] == 1.
    assert len(metadata['parameter_events']) == 1
