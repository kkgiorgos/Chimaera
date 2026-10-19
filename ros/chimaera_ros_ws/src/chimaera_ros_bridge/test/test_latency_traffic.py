import csv
import json
from pathlib import Path
import tempfile
import unittest

from chimaera_ros.latency import UNSET, make_report, read_events, routes, summarize, write_json
from chimaera_ros.latency_config import DEFAULT, plan_sweep
from chimaera_ros.latency_dashboard import samples
from chimaera_ros.latency_workloads import image_age, traffic


class BallTrafficTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = plan_sweep(dict(DEFAULT, workload='ball_catching',
            modes=['native_free']))['cases'][0]['config']
        self.config.update(status='complete', run_id='native')

    def fixture(self, name='native', changes=None):
        directory = self.root/name
        directory.mkdir()
        write_json(directory/'run.json', dict(self.config, **(changes or {})))
        with (directory/'events.csv').open('w') as handle:
            writer = csv.writer(handle)
            writer.writerow(['side', 'event', 'sequence', 'scheduled_ns', 'publish_ns',
                             'callback_ns', 'physics_ns', 'payload_bytes', 'stream'])
            writer.writerows([
                ['host','send',9,100000000,100000000,UNSET,100000000,921656,'left_image'],
                ['guest','receive',9,100000000,UNSET,110000000,UNSET,921656,'left_image'],
                ['host','send',9,100000000,100000000,UNSET,100000000,921660,'right_image'],
                ['guest','receive',9,100000000,UNSET,120000000,UNSET,921660,'right_image'],
                ['guest','send',25,100000000,101000000,UNSET,UNSET,224,'effort_command'],
                ['host','receive',25,100000000,UNSET,106000000,100000000,224,'effort_command']])
        return directory

    def test_saved_experiment_matches_application_traffic(self):
        path = Path(__file__).resolve().parents[1]/'latency_web/experiments/ball-catching.json'
        plan = plan_sweep(json.loads(path.read_text()))
        self.assertEqual(plan['counts'], dict(native_free=1, native_stepped=3, chimaera=5))
        self.assertEqual(plan['config']['gem5_configs'][0]['num_cores'], 1)
        streams = {s['name']:s for s in plan['traffic']}
        self.assertEqual(streams['left_image']['body_bytes'], 640*480*3)
        self.assertEqual(streams['right_image']['rate_hz'], 90.)
        self.assertEqual(streams['joint_states']['rate_hz'], 250.)
        self.assertEqual(streams['effort_command']['direction'], 'guest_to_host')
        self.assertEqual(streams['clock']['rate_hz'], 1000.)

    def test_all_streams_run_concurrently_in_each_case(self):
        plan = plan_sweep(dict(DEFAULT, workload='ball_catching', payload_bytes=[64,1024], rate_hz=[20,100]))
        self.assertEqual(plan['total'], 7)
        self.assertTrue(all(len(traffic(case['config']))==5 for case in plan['cases']))

    def test_bridge_matches_stereo_replacement_and_sensor_qos(self):
        result = routes(config=self.config)['topics']
        images = [r for r in result if r['type']=='sensor_msgs/msg/Image']
        self.assertEqual(len(images), 2)
        self.assertTrue(all(r['coalesce'] and r['coalesce_group']=='stereo' for r in images))
        self.assertTrue(all(r['qos']['depth']==2 and r['qos']['reliability']=='best_effort' for r in images))
        joint = [r for r in result if r['type']=='sensor_msgs/msg/JointState']
        self.assertTrue(all(not r['coalesce'] and r['qos']['depth']==1 for r in joint))
        self.assertTrue(all(r['host_topic'].startswith('/latency/') for r in result))
        self.assertEqual(image_age(self.config), 22222223)

    def test_invalid_image_and_control_shapes_are_rejected(self):
        for settings in [dict(camera_width=16), dict(camera_width=4096,camera_height=4096),
                         dict(camera_hz=float('inf')), dict(control_hz=1000), dict(image_age_periods=0)]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                plan_sweep(dict(DEFAULT, workload='ball_catching', ball_catching=settings))

    def test_typed_receipts_join_sources_without_extra_message_fields(self):
        directory = self.fixture()
        original = (directory/'events.csv').read_bytes()
        result = summarize(directory)
        self.assertEqual(result['streams']['left_image']['latency']['mean_ms'], 10.)
        self.assertEqual(result['streams']['right_image']['latency']['mean_ms'], 20.)
        self.assertEqual(result['streams']['effort_command']['latency']['mean_ms'], 5.)
        self.assertEqual(result['directions']['host_to_guest']['received'], 2)
        self.assertEqual((directory/'events.csv').read_bytes(), original)
        self.assertTrue(all(e['publish_ns']!=UNSET for e in read_events(directory)))

    def test_distribution_selects_one_stream(self):
        result = samples(self.fixture(), 'host_to_guest', 'right_image')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['cdf'], [[20., 1.]])

    def test_baselines_are_matched_per_stream_and_workload(self):
        self.fixture()
        self.fixture('stepped', dict(mode='native_stepped',run_id='stepped'))
        self.fixture('measured', dict(mode='chimaera',run_id='measured'))
        self.fixture('different_policy', dict(ball_catching=dict(self.config['ball_catching'],image_age_periods=3),run_id='other'))
        result = make_report(self.root)
        self.assertEqual(len(result['comparisons']), 10)
        self.assertEqual({c['stream'] for c in result['comparisons']}, {s['name'] for s in traffic(self.config)})
        self.assertNotIn('other', {c['baseline_id'] for c in result['comparisons']})
        with (self.root/'latency-report.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 20)
        self.assertEqual({row['stream'] for row in rows}, {s['name'] for s in traffic(self.config)})


if __name__ == '__main__':
    unittest.main()
