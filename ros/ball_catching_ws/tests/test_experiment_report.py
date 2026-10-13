import csv
import json
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report import load_trial, create_dashboard, _stats, _cadence


HOME = [0., 0., 0., -.14, 0., .50, math.pi/4]


def write_trial(directory, success=True):
    (directory / 'result.json').write_text(json.dumps(dict(success=success,
        reason='inside_cup_at_deadline' if success else 'outside_cup_at_deadline',
        launch_time=1., result_time=3., duration=2., contained_duration=1.9 if success else 0.,
        flight_end_time=1.1 if success else 3.,
        flight_end_kind='cup_capture' if success else 'duration_elapsed')))
    (directory / 'experiment.json').write_text(json.dumps(dict(launch_speed=3.,
        launch_position=[1.8, .04, 3.], initial_pose=HOME,
        start_condition='vertical_home', camera_hz=90., duration=2.)))
    (directory / 'interception.jsonl').write_text(json.dumps(dict(time=1.02, status='planned',
        intercept_time=1.1, braking_start_time=1.1, braking_end_time=1.5,
        cup_velocity=[-.3, 0., -.4], planning_wall_seconds=.005))+'\n')
    (directory / 'perception.jsonl').write_text(''.join(json.dumps(dict(capture_time=t,
        receive_time=t+.01, processing_wall_seconds=.003, observation=[.6, 0., .8],
        image_conversion_wall_seconds=.0001, detection_wall_seconds=.002,
        estimation_wall_seconds=.0005, publication_wall_seconds=.0004, state=[0]*6))+'\n'
        for t in [1.01, 1.02]))
    (directory / 'control_timing.jsonl').write_text(''.join(json.dumps(dict(time=t,
        processing_wall_seconds=.0002))+'\n' for t in [1.0, 1.004, 1.008]))
    fields = ['time', 'ball_x', 'ball_y', 'ball_z', 'launched', 'ball_vx', 'ball_vy', 'ball_vz',
              'inside', 'cup_x', 'cup_y', 'cup_z', *[f'fr3_joint{i+1}' for i in range(7)]]
    with (directory / 'ground_truth.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for time, vx, inside in [(1., -3., 0), (1.004, -3., 0), (1.008, 0., 0),
                                (1.1, 0., int(success)), (3., 0., int(success))]:
            writer.writerow(dict(time=time, ball_x=.6, ball_y=0., ball_z=.8, launched=1,
                ball_vx=vx, ball_vy=0., ball_vz=0., inside=inside,
                cup_x=.4 if time==1. else .42, cup_y=0., cup_z=.8,
                **{f'fr3_joint{i+1}': q for i, q in enumerate(HOME)}))
    (directory / 'robot.urdf').write_text('''<robot name="cup">
        <link name="base"><visual><geometry><box size="0.1 0.1 0.1"/></geometry></visual></link>
        </robot>''')


def test_statistics_convert_seconds_and_cadence_uses_timestamps():
    stats = _stats([.001, .002, .003, .010, None, float('nan')])
    assert stats['samples'] == 4
    assert stats['minimum_ms'] == 1.
    assert stats['median_ms'] == 2.5
    assert stats['mean_ms'] == 4.
    assert stats['p95_ms'] == stats['p99_ms'] == stats['maximum_ms'] == 10.
    cadence = _cadence([dict(time=t) for t in [1., 1.01, 1.03, 1.03]], 'time')
    assert cadence['rate_hz'] == pytest.approx(1/.015)
    assert cadence['gaps']['p95_ms'] == pytest.approx(20.)
    assert _cadence([dict(time=1.)], 'time') is None


def test_report_measures_arrival_before_impact_and_preserves_cup_timing(tmp_path):
    write_trial(tmp_path)
    trial = load_trial(tmp_path)
    summary = trial['summary']
    assert summary['evaluation_seconds'] == 2.
    assert summary['duration'] == 2.
    assert summary['contained_duration'] == 1.9
    assert summary['arrival_speed'] == 3.
    assert summary['arrival_speed_kind'] == 'before_inferred_impact'
    assert summary['flight_seconds'] == pytest.approx(.1)
    assert summary['reaction_seconds'] == pytest.approx(.09)
    assert summary['cup_position_at_launch'] == [.4, 0., .8]
    assert summary['cup_displacement_to_flight_end'] == pytest.approx(.02)
    assert summary['home_error_at_launch_degrees'] == 0.
    assert summary['braking_start_seconds'] == pytest.approx(.1)
    assert summary['braking_duration'] == pytest.approx(.4)
    assert summary['planned_cup_speed'] == .5
    assert summary['processing']['Planning']['p95_ms'] == 5.
    details = {row['name']: row for row in summary['timing_detail']}
    assert details['Arm control update']['desired_hz'] == 250.
    assert details['Arm control update']['achieved_hz'] == pytest.approx(250.)
    assert details['Capture → matched stereo callback']['stats']['median_ms'] == pytest.approx(10.)
    assert trial['frames'][0]['time'] == 0.
    assert trial['frames'][0]['joints']['fr3_joint4'] == -.14
    assert trial['frames'][-1]['time'] == pytest.approx(2.)


def test_deadline_miss_does_not_become_a_successful_flight_window(tmp_path):
    write_trial(tmp_path, success=False)
    summary = load_trial(tmp_path)['summary']
    assert summary['success'] is False
    assert summary['end_kind'] == 'duration_elapsed'
    assert summary['flight_seconds'] is None
    assert summary['reaction_seconds'] is None
    assert summary['cup_displacement_to_flight_end'] is None


def test_invalid_launch_cannot_be_reported_as_a_completed_task_miss(tmp_path):
    write_trial(tmp_path, success=False)
    raw_result = (tmp_path / 'result.json').read_bytes()
    (tmp_path / 'launch_validation.json').write_text(
        '{"valid":false,"reason":"launch_pose_or_velocity_reset"}')
    with pytest.raises(ValueError, match='Invalid ball launch'):
        load_trial(tmp_path)
    assert (tmp_path / 'result.json').read_bytes() == raw_result


@pytest.mark.parametrize('recorded', [False, True])
def test_missing_guest_logs_are_distinct_from_recorded_zero_counts(tmp_path, recorded):
    write_trial(tmp_path)
    for name in ('perception.jsonl', 'interception.jsonl'):
        if recorded:
            (tmp_path / name).write_text('')
        else:
            (tmp_path / name).unlink()
    summary = load_trial(tmp_path)['summary']
    if recorded:
        assert summary['perception_counts'] == dict(pairs=0, detected=0, tracked=0)
        assert summary['planning_counts'] == dict(updates=0, planned=0, infeasible=0)
    else:
        assert summary['perception_counts'] is None
        assert summary['planning_counts'] is None


def test_dashboard_embeds_cup_report_and_assets(tmp_path):
    write_trial(tmp_path)
    destination = create_dashboard([tmp_path], tmp_path/'dashboard.html')
    page = destination.read_text()
    assert 'Chimaera · Cup catching' in page
    assert 'window.EXPERIMENT_DATA = {' in page
    assert '/* EXPERIMENT_DATA */' not in page
    assert '/* REPLAY_VIEWER */' not in page
    assert '"root_links":["base"]' in page
    assert json.loads((tmp_path/'summary.json').read_text())['success'] is True


def test_report_excludes_processing_and_motion_after_final_evaluation(tmp_path):
    write_trial(tmp_path)
    for name, record in [
        ('control_timing.jsonl', dict(time=3.1, processing_wall_seconds=1.)),
        ('interception.jsonl', dict(time=3.1, status='planned', intercept_time=4.,
                                   planning_wall_seconds=1.)),
        ('perception.jsonl', dict(capture_time=3.1, receive_time=3.11, state=[0]*6,
                                 processing_wall_seconds=1.)),
        ('perception.jsonl', dict(capture_time=2.99, receive_time=3.01, state=[0]*6,
                                 processing_wall_seconds=1.))]:
        with (tmp_path/name).open('a') as output:
            output.write(json.dumps(record)+'\n')
    with (tmp_path/'ground_truth.csv').open() as source:
        lines = source.readlines()
    fields = lines[-1].strip().split(',')
    fields[0] = '3.1'
    with (tmp_path/'ground_truth.csv').open('a') as output:
        output.write(','.join(fields)+'\n')
    trial = load_trial(tmp_path)
    summary = trial['summary']
    assert summary['processing']['Arm control']['samples'] == 3
    assert summary['processing']['Arm control']['p95_ms'] == .2
    assert summary['planning_counts']['planned'] == 1
    assert summary['perception_counts']['pairs'] == 2
    assert trial['frames'][-1]['time'] == 2.
