import unittest

from common import PERFORMANCE_VARIANTS, validate_config
from matrix import short_matrix
from metrics import expected_releases


class ShortMatrixTests(unittest.TestCase):
    def test_short_screen_covers_all_prior_without_stepping_or_duplicate_references(self):
        matrix = short_matrix()
        cases = matrix['cases']
        self.assertEqual(len(cases), 12)
        self.assertEqual(len({c['name'] for c in cases}), 12)
        self.assertEqual({c['implementation'] for c in cases if c['cell'] == 'short-ball'
                          and c['config']['mode'] == 'chimaera'}, set(PERFORMANCE_VARIANTS))
        self.assertEqual(sum(c['config']['mode'] == 'chimaera' for c in cases), 10)
        self.assertEqual(sum(c['config']['mode'] == 'native_free' for c in cases), 2)
        self.assertFalse(any(c['config']['mode'] == 'native_stepped' for c in cases))
        declared = set(matrix['native_references']['by_cell'].values())
        actual = {c['name'] for c in cases if c['config']['mode'] == 'native_free'}
        self.assertEqual(declared, actual)

    def test_bulk_is_measured_inside_short_source_window_with_matching_profile(self):
        cases = short_matrix()['cases']
        for case in cases:
            config = case['config']
            validate_config(config)
            self.assertAlmostEqual(config['warmup'] + config['duration'] + config['drain'], .35)
            if case['cell'] != 'short-bulk8m':
                continue
            p = config['benchmark_parameters']
            self.assertEqual(expected_releases(50000000, 250000000,
                                               p['bulk_hz'], p['bulk_burst']), 1)
            baseline = next(c for c in cases if c['cell'] == 'short-ball'
                            and c['implementation'] == case['implementation']
                            and c['config']['mode'] == config['mode'])
            before = dict(baseline['config'])
            after = dict(config)
            before.pop('benchmark_parameters')
            after.pop('benchmark_parameters')
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
