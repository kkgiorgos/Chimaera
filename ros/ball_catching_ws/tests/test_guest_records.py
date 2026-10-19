import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guest_records import import_guest_records, timing_coverage


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


@pytest.mark.parametrize('bad', [envelope('control_timing', -1),
    envelope('control_timing', True), envelope('control_timing', 1.5), envelope('../result'),
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


def test_sequence_gaps_preserve_records_and_report_partial_coverage(tmp_path):
    records = [envelope('control_timing'), envelope('perception', 2),
               envelope('control_timing', 2), envelope('control_timing', 5)]
    write_capture(tmp_path, records)
    raw = (tmp_path / 'guest_records.jsonl').read_bytes()
    (tmp_path / 'commands.csv').write_text('receipt_time,command_time\n1.01,1\n')
    (tmp_path / 'result.json').write_text('{"launch_time":1,"result_time":2}')
    summary = import_guest_records(tmp_path)
    assert summary['status'] == 'partial'
    assert summary['sequence_gaps'] == 5
    assert summary['missing_sequences']['control_timing'] == [
        dict(first=1, last=1), dict(first=3, last=4)]
    assert summary['missing_sequences']['perception'] == [dict(first=0, last=1)]
    assert summary['records']['control_timing'] == 3
    assert summary['coverage']['status'] == 'partial'
    assert summary['coverage']['missing_control_timings'] == 0
    assert len((tmp_path / 'control_timing.jsonl').read_text().splitlines()) == 3
    assert (tmp_path / 'guest_records.jsonl').read_bytes() == raw
    assert timing_coverage(tmp_path, 1., 2.) == summary['coverage']


@pytest.mark.parametrize('sequences', [[0, 0], [0, 2, 1]])
def test_duplicate_or_reversed_sequences_still_fail_before_writing(tmp_path, sequences):
    write_capture(tmp_path, [envelope('perception'),
                             *[envelope('control_timing', seq) for seq in sequences]])
    with pytest.raises(ValueError, match='Invalid guest telemetry sequence'):
        import_guest_records(tmp_path)
    assert not (tmp_path / 'perception.jsonl').exists()


def test_missing_sequence_is_reported_without_command_capture(tmp_path):
    write_capture(tmp_path, [envelope('perception'), envelope('control_timing', 1)])
    (tmp_path / 'result.json').write_text('{"launch_time":1,"result_time":2}')
    summary = import_guest_records(tmp_path)
    assert summary['coverage']['status'] == 'partial'
    assert summary['coverage']['sequence_gaps'] == 1
    assert 'missing_control_timings' not in summary['coverage']


def test_gap_before_launch_is_distinct_from_task_command_coverage(tmp_path):
    records = [envelope('control_timing', sequence) for sequence in range(62)]
    for record in records:
        record['record'].update(time=.4165, completed_time=.417272291)
    records += [envelope('control_timing', 63), envelope('control_timing', 64),
                envelope('perception')]
    records[62]['record'].update(time=.4245, completed_time=.425322275)
    write_capture(tmp_path, records)
    (tmp_path / 'commands.csv').write_text('receipt_time,command_time\n1.01,1\n')
    (tmp_path / 'result.json').write_text('{"launch_time":0.4815,"result_time":1.9815}')
    summary = import_guest_records(tmp_path)
    assert summary['status'] == 'partial'
    assert summary['sequence_gaps'] == 1
    assert summary['coverage']['missing_control_timings'] == 0
    assert summary['missing_sequences']['control_timing'][-1] == dict(first=62, last=62)
    assert summary['coverage']['streams']['control_timing']['completed_records'] == 1


@pytest.mark.parametrize('complete', [False, True])
def test_contiguous_prefix_can_still_have_an_unexported_tail(tmp_path, complete):
    records = [envelope('control_timing'), envelope('perception')]
    if complete:
        tail = envelope('control_timing', 1)
        tail['record'].update(time=1.8, completed_time=1.802)
        records.append(tail)
    write_capture(tmp_path, records)
    (tmp_path / 'commands.csv').write_text(
        'receipt_time,command_time\n1.01,1\n1.81,1.8\n2.1,2.09\n')
    (tmp_path / 'result.json').write_text('{"launch_time":1,"result_time":2}')
    summary = import_guest_records(tmp_path)
    coverage = summary['coverage']
    assert summary['sequence_gaps'] == 0
    assert coverage['delivered_command_stamps'] == 2
    assert coverage['missing_control_timings'] == (0 if complete else 1)
    assert coverage['status'] == ('no_missing_delivered_control_timings' if complete else 'partial')
    if not complete:
        assert coverage['first_missing_command_time'] == 1.8
    assert coverage['streams']['control_timing']['last_completion_time'] == (1.802 if complete else 1.002)


def test_native_empty_capture_does_not_claim_guest_coverage(tmp_path):
    assert timing_coverage(tmp_path, 1., 2.) is None
    (tmp_path / 'guest_records.jsonl').write_text('')
    assert timing_coverage(tmp_path, 1., 2.) is None
    write_capture(tmp_path, [envelope('control_timing')])
    with pytest.raises(ValueError, match='missing perception'):
        import_guest_records(tmp_path)
