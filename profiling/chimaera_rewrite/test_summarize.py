import copy
import unittest

from summarize import summarize


def run(mode):
    metric = dict(published=30, received=29, delivery_fraction=29 / 30,
                  actual_source_hz=30, latency=dict(mean_ms=2, p95_ms=3),
                  release_lag=dict(mean_ms=1))
    return dict(path=mode + '/summary.json', variant='rewrite', physical_host='machine-a',
                config=dict(mode=mode, workload='ball_catching', payload_bytes=921600,
                            rate_hz=30, duration=1, warmup=.2, drain=.4, interval_us=2000,
                            poll_us=1000, cpu_type='timing', num_cores=2, wall_seconds=12),
                metrics=dict(left_image=metric))


class MatchedControls(unittest.TestCase):
    def test_requires_both_native_modes_on_same_machine_and_traffic(self):
        candidates = [run(mode) for mode in ('native_free', 'native_stepped', 'chimaera')]
        self.assertEqual(len(summarize(candidates)), 3)
        for field, value in (('physical_host', 'different-machine'),
                             ('variant', 'different-implementation')):
            changed = copy.deepcopy(candidates)
            changed[0][field] = value
            with self.assertRaisesRegex(ValueError, 'missing matched native_free'):
                summarize(changed)
        for field, value in (('rate_hz', 60), ('payload_bytes', 640 * 480), ('duration', 2)):
            changed = copy.deepcopy(candidates)
            changed[1]['config'][field] = value
            with self.assertRaisesRegex(ValueError, 'missing matched native_stepped'):
                summarize(changed)

    def test_empty_sources_cannot_look_like_low_latency_success(self):
        candidate = run('native_free')
        candidate['metrics']['left_image']['published'] = 0
        with self.assertRaisesRegex(ValueError, 'no source publications'):
            summarize([candidate])

    def test_delivery_counts_are_retained_alongside_latency(self):
        candidates = [run(mode) for mode in ('native_free', 'native_stepped', 'chimaera')]
        rows = summarize(candidates)
        simulated = rows[-1]
        self.assertEqual(simulated['received'], 29)
        self.assertEqual(simulated['native_free_delivery_fraction'], 29 / 30)
        self.assertEqual(simulated['native_stepped_case'], 'native_stepped/summary.json')


if __name__ == '__main__':
    unittest.main()
