import csv
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

from chimaera_ros.latency import UNSET, make_report, routes, stage_guest, statistics, summarize, validate


class LatencyAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = dict(status='complete', run_id='native', mode='native_free',
            payload_bytes=64, rate_hz=20., interval_us=50000, poll_us=10000,
            physics_step_ns=1000000, duration=1., warmup=1., drain=.5,
            reliability='reliable', depth=128, repeat=1, timeout=300)

    def write(self, directory=None, config=None, events=None):
        directory = directory or self.root
        directory.mkdir(exist_ok=True)
        (directory / 'run.json').write_text(json.dumps(config or self.config))
        with (directory / 'events.csv').open('w') as handle:
            writer = csv.writer(handle)
            writer.writerow(['side', 'event', 'sequence', 'scheduled_ns', 'publish_ns',
                             'callback_ns', 'physics_ns', 'payload_bytes'])
            writer.writerows(events if events is not None else [
                ['host', 'send', 0, 0, 0, UNSET, 0, 64],
                ['guest', 'receive', 0, 0, 0, 1200000000, UNSET, 64],
                ['host', 'send', 1, 1000000000, 1000000000, UNSET, 0, 64],
                ['guest', 'receive', 1, 1000000000, 1000000000, 1002000000, UNSET, 64],
                ['host', 'send', 2, 1100000000, 1150000000, UNSET, 0, 64],
                ['host', 'send', 3, 1900000000, 1900000000, UNSET, 0, 64],
                ['guest', 'receive', 3, 1900000000, 1900000000, 2100000000, UNSET, 64]])
        return directory

    def test_warmup_uses_source_time_and_late_receipts_are_included(self):
        metrics = summarize(self.write())['directions']['host_to_guest']
        self.assertEqual((metrics['published'], metrics['received'], metrics['missing_at_drain']), (3, 2, 1))
        self.assertEqual(metrics['latency']['mean_ms'], 101.)
        self.assertEqual(metrics['latency']['p99_ms'], 200.)
        self.assertAlmostEqual(metrics['release_lag']['mean_ms'], 50 / 3)

    def test_no_traffic_has_no_fabricated_zero_latency(self):
        result = summarize(self.write(events=[]))['directions']['guest_to_host']
        self.assertIsNone(result['latency']['mean_ms'])
        self.assertIsNone(result['delivery_fraction'])

    def test_duplicates_and_reordering_do_not_skew_latency(self):
        source = [['host', 'send', seq, 1000000000, 1000000000, UNSET, 0, 64] for seq in (1, 2)]
        received = [['guest', 'receive', seq, 1000000000, 1000000000, 1001000000, 0, 64] for seq in (2, 1, 2)]
        result = summarize(self.write(events=source + received))['directions']['host_to_guest']
        self.assertEqual((result['latency']['count'], result['duplicates'], result['reordered']), (2, 1, 1))

    def test_negative_latency_fails(self):
        with self.assertRaisesRegex(ValueError, 'Negative'):
            summarize(self.write(events=[
                ['host', 'send', 1, 1000000000, 1000000000, UNSET, 0, 64],
                ['guest', 'receive', 1, 1000000000, 1000000000, 999999999, 0, 64]]))

    def test_unmatched_receipt_fails(self):
        with self.assertRaisesRegex(ValueError, 'match'):
            summarize(self.write(events=[['guest', 'receive', 1, 0, 0, 1, 0, 64]]))

    def test_truncated_run_cannot_be_summarized(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            summarize(self.write(config=dict(self.config, status='failed')))

    def test_independent_differences_match_controls_and_failed_runs_are_visible(self):
        self.write(self.root / 'native')
        self.write(self.root / 'stepped', dict(self.config, mode='native_stepped', run_id='stepped'))
        self.write(self.root / 'other_interval', dict(self.config, mode='native_stepped', interval_us=20000))
        self.write(self.root / 'measured', dict(self.config, mode='chimaera', run_id='measured'))
        self.write(self.root / 'failed', dict(self.config, status='failed'))
        report = make_report(self.root)
        self.assertEqual(len(report['comparisons']), 4)
        self.assertEqual(len(report['excluded']), 1)
        self.assertEqual(report['comparisons'][0]['differences_ms']['mean_ms'], 0.)
        self.assertTrue((self.root / 'latency-report.html').is_file())

    def test_empty_and_nearest_rank_percentiles(self):
        self.assertIsNone(statistics([])['p99_ms'])
        self.assertEqual(statistics([1000000, 4000000, 2000000, 3000000])['p50_ms'], 2.)

    def test_baseline_from_another_collector_build_is_not_subtracted(self):
        self.write(self.root / 'native', dict(self.config,
            artifact_sha256={'/native/latency_endpoint': 'old'}))
        self.write(self.root / 'measured', dict(self.config, mode='chimaera', run_id='measured',
            artifact_sha256={'/measured/latency_endpoint': 'new'}))
        self.assertEqual(make_report(self.root)['comparisons'], [])

    def test_invalid_inputs_rejected(self):
        validate(self.config)
        for changes in (dict(rate_hz=float('nan')), dict(poll_us=50000),
                        dict(physics_step_ns=3000000), dict(payload_bytes=-1),
                        dict(duration=3601), dict(rate_hz=10000, duration=101)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(dict(self.config, **changes))

    def test_route_configuration_preserves_data_and_reliable_telemetry(self):
        result = routes('best_effort', 5)
        self.assertEqual(result['version'], 1)
        self.assertEqual(len(result['topics']), 3)
        self.assertFalse(any(route.get('coalesce') for route in result['topics']))
        self.assertEqual(result['topics'][0]['qos']['reliability'], 'best_effort')
        self.assertEqual(result['topics'][2]['qos']['reliability'], 'reliable')

    def test_guest_bundle_has_relocated_setup_and_per_case_routes(self):
        package = self.root / 'package'
        binary = package / 'lib/chimaera_ros_bridge/guest_bridge'
        binary.parent.mkdir(parents=True)
        binary.write_text('test executable')
        capabilities = package/'share/chimaera_ros_bridge/config/latency_capabilities.json'
        capabilities.parent.mkdir(parents=True)
        capabilities.write_text('{"version":3}')
        output = self.root / 'stage'
        bundle = self.root / 'guest.tar.gz'
        stage_guest(output, package, bundle)
        guest = json.loads((output / 'opt/chimaera/latency/session.json').read_text())
        self.assertEqual(guest['routes'], '/tmp/chimaera-latency-routes.json')
        self.assertTrue(guest['bridge']['simulation_time_from_readfile'])
        self.assertTrue(guest['guest']['setup'][1].endswith('/share/chimaera_ros_bridge/local_setup.bash'))
        self.assertIn('run /tmp/chimaera-latency-session.json --side guest',
            (output / 'opt/chimaera/latency/guest_start').read_text())
        with tarfile.open(bundle) as archive:
            self.assertIn('opt/chimaera/latency/guest_start', archive.getnames())
            marker = json.load(archive.extractfile('opt/chimaera/latency/latency-runtime.json'))
            self.assertEqual(marker, dict(version=3, cpu_types=['kvm', 'timing', 'o3'], workloads=['synthetic', 'ball_catching']))
            self.assertTrue(all(not name.startswith('/') and '..' not in Path(name).parts
                                for name in archive.getnames()))
            self.assertTrue(all(member.mtime == 0 for member in archive.getmembers()))


if __name__ == '__main__':
    unittest.main()
