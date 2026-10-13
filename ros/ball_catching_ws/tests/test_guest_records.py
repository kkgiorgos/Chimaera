import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guest_records import import_guest_records


def envelope(name, sequence=0):
    return dict(stream=name, sequence=sequence, record=dict(
        time=1., completed_time=1.002, timing_clock='gem5 simulation',
        processing_simulation_seconds=.002))


def write_capture(directory, records):
    (directory / 'guest_records.jsonl').write_text(
        ''.join(json.dumps(r) + '\n' for r in records))


def test_recovers_interleaved_streams_and_preserves_clock(tmp_path):
    write_capture(tmp_path, [envelope('control_timing'), envelope('perception'),
                             envelope('control_timing', 1)])
    summary = import_guest_records(tmp_path)
    assert summary['sequence_gaps'] == 0
    assert summary['records']['control_timing'] == 2
    assert (tmp_path / 'interception.jsonl').read_text() == ''
    assert len((tmp_path / 'control_timing.jsonl').read_text().splitlines()) == 2
    assert json.loads((tmp_path / 'perception.jsonl').read_text())['timing_clock'] == 'gem5 simulation'


@pytest.mark.parametrize('bad', [envelope('control_timing', 2), envelope('../result'),
    dict(stream='control_timing', sequence=0, record=dict(completed_time=1., timing_clock='wall')),
    dict(stream='control_timing', sequence=0, record=dict(completed_time=float('nan'),
                                                       timing_clock='gem5 simulation'))])
def test_rejects_corrupt_telemetry_before_writing_streams(tmp_path, bad):
    write_capture(tmp_path, [bad])
    with pytest.raises(ValueError):
        import_guest_records(tmp_path)
    assert not (tmp_path / 'control_timing.jsonl').exists()


def test_missing_streams_are_an_infrastructure_error(tmp_path):
    with pytest.raises(ValueError, match='not recorded'):
        import_guest_records(tmp_path)
    write_capture(tmp_path, [envelope('control_timing')])
    with pytest.raises(ValueError, match='missing perception'):
        import_guest_records(tmp_path)
