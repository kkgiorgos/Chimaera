import csv
import json
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report import load_trial, _stats, _cadence
from run_experiment import prepare_point


def test_seeded_targets_are_shared_across_speed_points():
    options = dict(position=[23.77, 0., 2.5], target=[.5, 0., .8],
                   jitter=[.02, .02, .01], seed=42, drag_coefficient=.55)
    slow, fast = prepare_point(20., **options), prepare_point(40., **options)
    assert len(slow) == len(fast) == 5
    assert [t['target'] for t in slow] == [t['target'] for t in fast]
    assert len({tuple(t['target']) for t in slow}) == 5
    assert all(t['predicted_arrival_speed'] < 40. for t in fast)


def test_detailed_timing_statistics_convert_seconds_and_cadence_uses_timestamps():
    stats = _stats([.001, .002, .003, .010, None, float('nan')])
    assert stats['samples'] == 4
    assert stats['minimum_ms'] == 1.
    assert stats['median_ms'] == 2.5
    assert stats['mean_ms'] == 4.
    assert stats['p95_ms'] == stats['p99_ms'] == stats['maximum_ms'] == 10.
    cadence = _cadence([dict(time=t) for t in [1.0, 1.01, 1.03, 1.03]], 'time')
    assert cadence['rate_hz'] == pytest.approx(1/.015)
    assert cadence['gaps']['p95_ms'] == pytest.approx(20.)
    assert _cadence([dict(time=1.)], 'time') is None


def test_impossible_point_and_invalid_jitter_are_rejected():
    with pytest.raises(ValueError, match='no floor/net-clearing trajectory'):
        prepare_point(3., [23.77, 0, 2.5], [.5, 0, .8], [.02, .02, .01], 42, .55)
    with pytest.raises(ValueError, match='jitter must be nonnegative'):
        prepare_point(20., [23.77, 0, 2.5], [.5, 0, .8], [-1, 0, 0], 42, .55)


def test_gentle_profile_keeps_five_seeded_directions_and_physical_speeds():
    cases = prepare_point(1., [.5, 0, 1.1], [.5, 0, .8], [.02, .02, .01], 42, .55, court=False, arc='high')
    assert len(cases) == 5
    assert len({tuple(case['direction']) for case in cases}) == 5
    assert all(2.5 < case['predicted_arrival_speed'] < 2.8 for case in cases)
    assert all(case['direction'][2] > .98 for case in cases)
    assert all(case['predicted_flight_time'] > .36 for case in cases)


def test_report_uses_incoming_velocity_and_separates_reaction_window(tmp_path):
    (tmp_path / 'result.json').write_text(json.dumps(dict(success=False, reason='floor_contact',
        launch_time=1., result_time=3., retention=.2, flight_end_time=3., flight_end_kind='floor_contact')))
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(launch_speed=20.,
        launch_position=[23.77, 0, 2.5], target=[.5, 0, .8],
        initial_pose=[-1.37723, -1.10247, .784482, -2.10488, 2.74689, 2.49007, 2.19599])))
    (tmp_path / 'interception.jsonl').write_text(json.dumps(dict(time=1.5, status='planned',
        intercept_time=2.5, braking_start_time=2.5, braking_end_time=2.9,
        cup_velocity=[-.3, 0., -.4]))+'\n')
    (tmp_path / 'perception.jsonl').write_text(json.dumps(dict(capture_time=2., receive_time=2.01,
        processing_wall_seconds=.003, state=[0]*6))+'\n')
    fields = ['time', 'ball_x', 'ball_y', 'ball_z', 'launched', 'ball_vx', 'ball_vy', 'ball_vz',
              'left_contact', 'right_contact', 'fr3_joint1']
    with (tmp_path / 'ground_truth.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for time, x, vx, contact in [(1., 23.77, -20., 0), (2.4, .6, -12., 0), (2.5, .4, -2., 1), (3., .3, 0., 0)]:
            writer.writerow(dict(time=time, ball_x=x, ball_y=0., ball_z=.8, launched=1,
                ball_vx=vx, ball_vy=0., ball_vz=0., left_contact=contact, right_contact=contact, fr3_joint1=.1))
    trial = load_trial(tmp_path)
    assert trial['summary']['arrival_speed'] == 12.
    assert trial['summary']['flight_seconds'] == 2.
    assert trial['summary']['reaction_seconds'] == pytest.approx(.45)
    assert trial['summary']['processing']['Perception + estimation']['p95_ms'] == 3.
    assert trial['frames'][0]['joints']['fr3_joint1'] == .1
    assert trial['summary']['processing']['Planning'] is None
    assert trial['summary']['start_condition'] == 'historical_prepared_pose'
    assert trial['summary']['braking_start_seconds'] == 1.5
    assert trial['summary']['braking_end_seconds'] == 1.9
    assert trial['summary']['braking_duration'] == pytest.approx(.4)
    assert trial['summary']['planned_cup_speed'] == .5
    assert trial['summary']['home_error_at_launch_degrees'] is None
    details = {r['name']: r for r in trial['summary']['timing_detail']}
    assert details['Perception callback · total']['stats']['p95_ms'] == 3.
    assert details['Perception callback · total']['clock'] == 'wall'
    age = details['Capture → matched stereo callback']
    assert age['stats']['median_ms'] == pytest.approx(10.)
    assert age['clock'] == 'simulation'
    assert details['Image preparation']['stats'] is None


def test_report_does_not_call_post_palm_stop_the_arrival_speed(tmp_path):
    (tmp_path / 'result.json').write_text(json.dumps(dict(success=True, reason='retained',
        launch_time=1., result_time=1.216, retention=.2, flight_end_time=1.016, flight_end_kind='stable_capture')))
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(launch_speed=3.,
        launch_position=[.7, 0, .8], target=[.5, 0, .8])))
    fields = ['time', 'ball_x', 'ball_y', 'ball_z', 'launched', 'ball_vx', 'ball_vy', 'ball_vz',
              'left_contact', 'right_contact']
    with (tmp_path / 'ground_truth.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for time, vx, contact in [(1., -3., 0), (1.004, -3., 0), (1.008, 0., 0), (1.012, 0., 1), (1.216, 0., 1)]:
            writer.writerow(dict(time=time, ball_x=.6, ball_y=0., ball_z=.8, launched=1,
                ball_vx=vx, ball_vy=0., ball_vz=0., left_contact=contact, right_contact=contact))
    summary = load_trial(tmp_path)['summary']
    assert summary['arrival_speed'] == 3.
    assert summary['arrival_speed_kind'] == 'before_inferred_impact'


def test_vertical_arrival_plane_does_not_divide_by_constant_x(tmp_path):
    (tmp_path / 'result.json').write_text(json.dumps(dict(success=False, reason='floor_contact',
        launch_time=1., result_time=2., retention=.2, flight_end_time=2., flight_end_kind='floor_contact')))
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(launch_speed=1.5,
        launch_position=[.5, 0, 1.1], target=[.5, 0, .8])))
    (tmp_path / 'perception.jsonl').write_text(json.dumps(dict(capture_time=1.1, receive_time=1.11,
        processing_wall_seconds=.003, state=[0]*6))+'\n')
    fields = ['time', 'ball_x', 'ball_y', 'ball_z', 'launched', 'ball_vx', 'ball_vy', 'ball_vz']
    with (tmp_path / 'ground_truth.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for time, z, vz in [(1., 1.1, 1.5), (1.4, .9, -2.), (1.6, .7, -3.), (2., .03, 0.)]:
            writer.writerow(dict(time=time, ball_x=.5, ball_y=0., ball_z=z, launched=1,
                ball_vx=0., ball_vy=0., ball_vz=vz))
    summary = load_trial(tmp_path)['summary']
    assert summary['arrival_speed'] == 2.
    assert summary['reaction_seconds'] == pytest.approx(.4)


def test_court_report_excludes_ground_impulses_and_records_reacquisition(tmp_path):
    (tmp_path / 'result.json').write_text(json.dumps(dict(success=True, reason='retained',
        launch_time=1., result_time=2.1, retention=.2, flight_end_time=1.9,
        flight_end_kind='stable_capture', bounce_count=1)))
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(launch_speed=14.,
        launch_position=[23.77, 0, 2.5], target=[.5, 0, .8], allowed_bounces=1)))
    (tmp_path / 'bounces.jsonl').write_text(json.dumps(dict(time=1.1, count=1))+'\n')
    records = [dict(capture_time=t, receive_time=t+.01, state=state,
                    processing_wall_seconds=.002, track_reset=reset)
               for t, state, reset in [(1.05, [0]*6, 0), (1.12, None, 1), (1.2, [0]*6, 0)]]
    (tmp_path / 'perception.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    fields = ['time', 'ball_x', 'ball_y', 'ball_z', 'launched', 'ball_vx', 'ball_vy', 'ball_vz',
              'left_contact', 'right_contact']
    with (tmp_path / 'ground_truth.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
        for time, x, z, vx, vz, contact in [(1., 23.77, 2.5, -14., 0., 0),
                (1.098, 8., .06, -10., -6., 0), (1.102, 7.98, .06, -6., 4.5, 0),
                (1.8, .6, .8, -3., 0., 0), (1.9, .55, .8, 0., 0., 1), (2.1, .55, .8, 0., 0., 1)]:
            writer.writerow(dict(time=time, ball_x=x, ball_y=0., ball_z=z, launched=1,
                ball_vx=vx, ball_vy=0., ball_vz=vz, left_contact=contact, right_contact=contact))
    trial = load_trial(tmp_path)
    assert trial['summary']['arrival_speed'] == 3.
    assert trial['summary']['bounce_count'] == 1
    assert trial['summary']['post_bounce_track_seconds'] == pytest.approx(.1)
    assert trial['summary']['post_bounce_reaction_seconds'] == pytest.approx(.7)
    assert trial['bounces'][0]['time'] == pytest.approx(.1)


def test_presentation_loss_does_not_rewrite_a_successful_catch(tmp_path):
    (tmp_path / 'result.json').write_text(json.dumps(dict(success=True, reason='retained',
        launch_time=1., result_time=1.3, retention=.2, flight_end_time=1.1,
        flight_end_kind='stable_capture', presentation_end_time=3.5)))
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(launch_speed=1.5,
        launch_position=[.5, 0, 1.1], target=[.5, 0, .8], present=True,
        initial_pose=[0., -math.pi/4, 0., -3*math.pi/4, 0., math.pi/2, math.pi/4])))
    (tmp_path / 'interception.jsonl').write_text(json.dumps(dict(time=1.02,
        status='visual_approach', duration=.8, planning_wall_seconds=.005))+'\n')
    (tmp_path / 'presentation_done.json').write_text(json.dumps(dict(time=3.5, held=False)))
    fields = ['time', 'ball_x', 'ball_y', 'ball_z', 'launched', 'ball_vx', 'ball_vy', 'ball_vz',
              'left_contact', 'right_contact', 'inside', 'cup_x', 'cup_y', 'cup_z']
    with (tmp_path / 'ground_truth.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
        for time, z, contact, inside in [(1., 1.1, 0, 0), (1.1, .8, 1, 1), (3.5, .0335, 0, 0)]:
            writer.writerow(dict(time=time, ball_x=.5, ball_y=0., ball_z=z, launched=1,
                ball_vx=0., ball_vy=0., ball_vz=0., left_contact=contact, right_contact=contact, inside=inside,
                cup_x=.4 if time==1. else .42 if time==1.1 else .5, cup_y=0., cup_z=.8 if time<3.5 else 1.))
    summary = load_trial(tmp_path)['summary']
    assert summary['success'] is True
    assert summary['presentation_success'] is False
    assert summary['hand_position_at_launch'] == [.4, 0., .8]
    assert summary['hand_displacement_to_flight_end'] == pytest.approx(.02)
    assert summary['start_condition'] == 'upright_home'
    assert summary['visual_approach_seconds'] == pytest.approx(.02)
    assert summary['visual_approach_duration'] == .8
    assert summary['processing']['Planning']['p95_ms'] == 5.
    assert summary['flight_seconds'] == pytest.approx(.1)
