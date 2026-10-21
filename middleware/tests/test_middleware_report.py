import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'middleware_report', Path(__file__).resolve().parents[1] / 'report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def cohort():
    attempts = []
    for label in ('native_free', 'native_stepped', 'bridge', 'fastdds'):
        native = label.startswith('native')
        config = dict(mode=label if native else 'chimaera', workload='synthetic',
            middleware_transport=label if not native else 'bridge', cpu_type='timing',
            num_cores=2, cpu_clock='3GHz', duration=1., wall_seconds=100.,
            artifact_sha256={'/runtime/latency_endpoint': 'same'},
            transport_backend='dma', dds_tuning='large_shm_only', dds_max_message_bytes=1048576)
        mean = 2. if label == 'fastdds' else 4.
        metric = dict(published=10, received=10, missing_at_drain=0, duplicates=0, reordered=0,
            delivery_fraction=1., actual_source_hz=10., release_lag=dict(mean_ms=1.),
            latency=dict(count=10, mean_ms=mean, p95_ms=7. if label == 'fastdds' else 6.))
        attempts.append(dict(case=label, result='complete', summary=dict(config=config,
            directions={'host_to_guest': metric}), cpu_origin=dict(
            active_cpus=['BaseTimingSimpleCPU'] * 2), provenance=dict(host='container',
            gem5_sha256='same-simulator')))
    return dict(comparisons_validated=True, physical_host='test', attempts=attempts)


class MiddlewareReportTest(unittest.TestCase):
    def test_mean_reduction_retains_tail_regression_and_counts(self):
        row = report.compare(cohort())['rows'][0]
        self.assertEqual(row['mean_reduction_percent'], 50.)
        self.assertEqual(row['metrics']['fastdds']['latency']['p95_ms'], 7.)
        self.assertEqual(row['metrics']['bridge']['received'], 10)

    def test_incomplete_delivery_cannot_be_reported_as_an_improvement(self):
        data = cohort()
        metric = data['attempts'][-1]['summary']['directions']['host_to_guest']
        metric.update(received=9, missing_at_drain=1, delivery_fraction=.9)
        row = report.compare(data)['rows'][0]
        self.assertFalse(row['delivery_valid'])
        self.assertIsNone(row['mean_reduction_percent'])

    def test_mismatched_hardware_or_unverified_cpu_is_rejected(self):
        data = cohort()
        data['attempts'][-1]['summary']['config']['cpu_clock'] = '2GHz'
        with self.assertRaisesRegex(ValueError, 'settings differ'):
            report.compare(data)
        data = cohort()
        data['attempts'][-1]['cpu_origin']['active_cpus'] = ['X86KvmCPU'] * 2
        with self.assertRaisesRegex(ValueError, 'CPU provenance'):
            report.compare(data)

    def test_unmatched_dds_profile_or_simulator_is_rejected(self):
        data = cohort()
        data['attempts'][-1]['summary']['config']['dds_max_message_bytes'] = 65500
        with self.assertRaises(ValueError):
            report.compare(data)
        data = cohort()
        data['attempts'][-1]['provenance']['gem5_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'settings differ'):
            report.compare(data)


if __name__ == '__main__':
    unittest.main()
