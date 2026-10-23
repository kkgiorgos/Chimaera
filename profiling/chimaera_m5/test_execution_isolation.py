import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from common import GEOMETRY, validate_config
from isolation_matrix import isolation_matrix, copy_mode_matrix


class ExecutionIsolationTests(unittest.TestCase):
    def test_report_does_not_mix_unloaded_modeling_boundaries(self):
        from isolation_report import report
        def row(cell, mode, value):
            metric = dict(age=dict(p99_ms=value), missed_source_releases=0,
                          missing_at_drain=0, received=50)
            return dict(status='complete', errors=[], config=dict(middleware_copy_mode=mode),
                        case=dict(name=cell + mode, cell=cell, execution_layout='split'),
                        guest_statistics=dict(completed_dumps=[{
                            'board.chimaera_m5.functionalCopies.copyCalls': 1 if mode == 'functional' else 0,
                            'board.chimaera_m5.functionalCopies.copyBytes': 1 if mode == 'functional' else 0,
                            'board.chimaera_m5.functionalCopies.fillCalls': 0,
                            'board.chimaera_m5.functionalCopies.fillBytes': 0,
                            'board.chimaera_m5.functionalCopies.faults': 0}]),
                        metrics={name: metric for name in ('joint_states', 'effort_command', 'clock')})
        inherited = 'Guest serialization and DDS work remain modeled; no host middleware offload.'
        suite = dict(matrix=dict(stage='test', limitations=[inherited]))
        rows = [row('ball', 'guest', 1), row('ball', 'functional', 10),
                row('bulk8m', 'functional', 11)]
        with patch('isolation_report.collect', return_value=(suite, rows)):
            result = report(Path('test'))
        self.assertTrue(all(r['loaded_minus_baseline_age_p99_ms'] == [1]
                            for r in result['load_contrasts']))
        self.assertEqual(result['recorded_matrix_limitations'], [inherited])
        self.assertNotIn('no host middleware offload', ' '.join(result['limitations']))

    def test_report_rejects_missing_fault_counters_and_faulted_copies(self):
        from isolation_report import report
        prefix = 'board.chimaera_m5.functionalCopies.'
        good = {prefix + name: value for name, value in (
            ('copyCalls', 1), ('copyBytes', 1024), ('fillCalls', 0), ('fillBytes', 0), ('faults', 0))}
        suite = dict(matrix=dict(stage='test', limitations=[]))
        for change in ('missing_faults', 'faults', 'unobserved', 'guest_activity'):
            counters = dict(good)
            mode = 'functional'
            if change == 'missing_faults':
                counters.pop(prefix + 'faults')
            elif change == 'faults':
                counters[prefix + 'faults'] = 1
            elif change == 'unobserved':
                counters = dict.fromkeys(counters, 0)
            else:
                mode = 'guest'
            row = dict(status='complete', errors=[], config=dict(middleware_copy_mode=mode),
                       case=dict(name=change), guest_statistics=dict(completed_dumps=[counters]))
            with self.subTest(change=change), patch('isolation_report.collect', return_value=(suite, [row])):
                result = report(Path('test'))
                self.assertFalse(result['copy_boundary_evidence'][0]['valid'])
                self.assertTrue(result['attempts'][0]['errors'])

    def test_requested_creation_trace_rejects_missing_timestamp_coverage(self):
        from isolation_report import report
        row = dict(status='complete', errors=[],
                   config=dict(benchmark_parameters=dict(trace_creation=True)),
                   case=dict(name='missing'),
                   metrics=dict(joint_states=dict(published=50, received=50,
                                                  creation_to_callback=dict(count=0),
                                                  preparation=dict(count=0))))
        suite = dict(matrix=dict(stage='test', limitations=[]))
        with patch('isolation_report.collect', return_value=(suite, [row])):
            result = report(Path('test'))
        self.assertIn('Incomplete creation', result['attempts'][0]['errors'][0])
        self.assertEqual(result['unloaded_latency_variability'], [])

    def test_copy_modes_match_all_other_settings_and_keep_one_bulk_worker(self):
        for cpu in ('kvm', 'timing'):
            cases = copy_mode_matrix(cpu)['cases']
            self.assertEqual(len(cases), 4)
            for guest, functional in zip(cases[::2], cases[1::2]):
                self.assertEqual(guest['config']['middleware_copy_mode'], 'guest')
                self.assertEqual(functional['config']['middleware_copy_mode'], 'functional')
                functional['config']['middleware_copy_mode'] = 'guest'
                self.assertEqual(guest['config'], functional['config'])
                self.assertTrue(guest['config']['benchmark_parameters']['isolate_bulk_execution'])

    def test_copy_modes_interleave_three_creation_traced_unloaded_windows(self):
        cases = copy_mode_matrix('timing', unloaded_repeats=3)['cases']
        self.assertEqual(len(cases), 8)
        self.assertTrue(all(case['cell'] == 'ball' for case in cases[:6]))
        self.assertEqual([case['config']['repeat'] for case in cases[:6]], [1, 1, 2, 2, 3, 3])
        self.assertTrue(all(case['config']['benchmark_parameters']['trace_creation'] for case in cases))
        self.assertEqual(len({case['name'] for case in cases}), 8)
        with self.assertRaises(ValueError):
            copy_mode_matrix('kvm', unloaded_repeats=3)

    def test_copy_preload_is_scoped_to_the_guest_endpoint_command(self):
        from adapter import install
        def session(*args):
            return dict(guest=dict(env={'keep': 'value'}, processes=[
                dict(name='latency_endpoint', command=['endpoint', '--ros-args']),
                dict(name='other', command=['other'])]))
        module = SimpleNamespace(parameters=lambda *args: None, routes=lambda *args: dict(topics=[]),
                                 traffic=lambda config: [], dds_profile=lambda config: None,
                                 validate_bundle=lambda *args: None, guest_session=session)
        install(module)
        for mode, value in (('guest', '0'), ('functional', '1')):
            result = module.guest_session('timing', dict(middleware_copy_mode=mode))
            self.assertEqual(result['guest']['env'], {'keep': 'value'})
            endpoint, other = result['guest']['processes']
            self.assertEqual(endpoint['command'][0], 'env')
            self.assertIn('CHIMAERA_FUNCTIONAL_MIDDLEWARE_COPIES=' + value, endpoint['command'])
            self.assertEqual(endpoint['command'][-2:], ['endpoint', '--ros-args'])
            self.assertEqual(other['command'], ['other'])

    def test_timing_round_repeats_unloaded_layouts_before_bulk(self):
        cases = isolation_matrix('timing')['cases']
        self.assertEqual(len(cases), 8)
        self.assertTrue(all(case['cell'] == 'ball' for case in cases[:6]))
        self.assertTrue(all(case['cell'] == 'bulk8m' for case in cases[6:]))
        self.assertEqual([case['config']['repeat'] for case in cases[:6]], [1, 1, 2, 2, 3, 3])
        for case in cases:
            validate_config(case['config'])
            self.assertEqual({key: case['config'][key] for key in GEOMETRY}, GEOMETRY)

    def test_layout_pairs_differ_only_in_isolation_parameter(self):
        for cpu in ('timing', 'kvm'):
            cases = isolation_matrix(cpu)['cases']
            for merged, split in zip(cases[::2], cases[1::2]):
                before, after = merged['config'], split['config']
                self.assertFalse(before['benchmark_parameters']['isolate_bulk_execution'])
                after['benchmark_parameters']['isolate_bulk_execution'] = False
                self.assertEqual(before, after)
                self.assertEqual(before['verify_payload'], cpu == 'kvm')

    def test_diagnostic_options_are_boolean_and_unknown_parameters_fail(self):
        for name, value in (('trace_publish', 1), ('isolate_bulk_execution', 'true'),
                            ('unknown_execution_option', False)):
            config = isolation_matrix('kvm')['cases'][0]['config']
            config['benchmark_parameters'][name] = value
            with self.assertRaises(ValueError):
                validate_config(config)


if __name__ == '__main__':
    unittest.main()
