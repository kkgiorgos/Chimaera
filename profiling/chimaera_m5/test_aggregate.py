"""Focused evidence/metric regressions. PREPARED ONLY; run after the catch gate."""
import csv
from copy import deepcopy
from pathlib import Path
import tempfile
import tarfile
import unittest

from aggregate import (collect, compare, cpu_evidence, gem5_stats, load_assessments,
                       native_matches, tail_decisions)
from common import (BALL, COMMON_FILES, GEOMETRY, IMPLEMENTATION_FILES, PARAMETERS, UNSET,
                    validate_config, write_json)
from freeze import bundle_common
from metrics import analyze, expected_releases, observation, read_events, statistics
from stage import stage
from run import check_gate


RULES = dict(trim_seconds=.25, observation_bin_seconds=.25, control_age_deadline_ms=10.,
    image_age_deadline_ms=33.333334, max_relative_source_rate_error=.05,
    max_backlog_growth_messages=1, max_oldest_age_growth_ms=2.,
    min_independent_windows=5, min_control_receipts=10000)
SHORT_RULES = dict(RULES, trim_seconds=0., observation_bin_seconds=.025,
                   bulk_observation_alignment='fixed_window')


def config(**overrides):
    result = dict(GEOMETRY, duration=2., warmup=.25, drain=.75, rate_hz=30.,
        payload_bytes=518400, physics_step_ns=1000000, interval_us=2000, poll_us=1000,
        single_boot=False, cpu_type='timing', mode='chimaera', delivery_policy='lossless',
        verify_payload=False, ball_catching=dict(BALL), workload='ball_catching',
        benchmark_parameters=dict(PARAMETERS), reliability='reliable', depth=128)
    result.update(overrides)
    return result


def short_config(**overrides):
    settings = dict(duration=.2, warmup=.05, drain=.1, repeat=1,
                    dds_tuning='large_shm_only', dds_shm_bytes=64 * 1024**2,
                    dds_max_message_bytes=9433088)
    settings.update(overrides)
    return config(**settings)


def event(kind, seq, stream='left_image', publish=300000000, callback=400000000,
          scheduled=300000000, size=518480):
    source = 'guest' if stream in ('effort_command', 'bulk_image_g2h') else 'host'
    return dict(side=source if kind == 'send' else ('guest' if source == 'host' else 'host'),
        event=kind, stream=stream, sequence=seq, scheduled_ns=scheduled, publish_ns=publish,
        callback_ns=callback if kind == 'receive' else UNSET, physics_ns=UNSET, payload_bytes=size)


class MetricsTests(unittest.TestCase):
    def test_warmup_receipts_remain_distinct_from_the_measured_publication_cohort(self):
        settings = dict(PARAMETERS, bulk_bytes=8 * 1024**2, bulk_hz=5., bulk_burst=1)
        events = [event('send', 0, stream='bulk_image_h2g', publish=0, scheduled=0),
                  event('receive', 0, stream='bulk_image_h2g', publish=0, scheduled=0,
                        callback=100000000),
                  event('send', 1, stream='bulk_image_h2g', publish=200000000, scheduled=200000000),
                  event('receive', 1, stream='bulk_image_h2g', publish=200000000,
                        scheduled=200000000, callback=300000000)]
        metric = analyze(events, short_config(benchmark_parameters=settings), SHORT_RULES)['bulk_image_h2g']
        self.assertEqual((metric['published'], metric['received']), (1, 1))
        self.assertEqual(metric['observation']['receipts'], 1)
        self.assertEqual(metric['observation']['receipt_publication_cohorts'],
                         dict(before_source_window=1, source_window=0))
        self.assertEqual(metric['observation']['same_source_window_body_MB_per_observation_second'], 0)
        self.assertGreater(metric['observation']['body_MB_per_observation_second'], 0)

    def test_drain_receipts_do_not_become_observed_goodput(self):
        events = [event('send', 0), event('receive', 0, callback=2500000000)]
        metric = analyze(events, config(), RULES)['left_image']
        self.assertEqual(metric['delivered_per_source_second'], .5)
        self.assertEqual(metric['observation']['receipts'], 0)
        self.assertEqual(metric['observation']['body_MB_per_observation_second'], 0)
        self.assertEqual(metric['observation']['max_outstanding_messages'], 1)
        self.assertGreater(metric['observation']['oldest_age_growth_ms'], 0)

    def test_actual_fixed_intervals_and_half_open_edges(self):
        sends = {0: event('send', 0, publish=0, scheduled=0),
                 1: event('send', 1, publish=0, scheduled=0)}
        receipts = {0: event('receive', 0, publish=0, scheduled=0, callback=1000000000),
                    1: event('receive', 1, publish=0, scheduled=0, callback=2000000000)}
        result = observation(sends, receipts, 100, 1000000000, 2000000000, 250000000)
        self.assertEqual(result['receipts'], 1)
        self.assertEqual(result['messages_per_observation_second'], 1)
        self.assertEqual(sum(row['receipts'] for row in result['bins']), 1)
        self.assertEqual(result['backlog'][-1]['outstanding_messages'], 1)

    def test_missing_remains_censored_and_deadline_visible(self):
        metric = analyze([event('send', 0)], config(), RULES)['left_image']
        self.assertEqual(metric['censored_sequence_ids'], [0])
        self.assertEqual(metric['missing_age_deadline_misses'], 1)
        self.assertIsNone(metric['latency']['p99_ms'])
        self.assertFalse(metric['bounded_observation'])

    def test_duplicates_and_order_do_not_improve_unique_goodput(self):
        events = [event('send', n) for n in range(3)]
        events += [event('receive', n, callback=600000000 + i) for i, n in enumerate((1, 0, 1, 2))]
        metric = analyze(events, config(), RULES)['left_image']
        self.assertEqual((metric['received'], metric['duplicates'], metric['reordered']), (3, 1, 1))
        self.assertEqual(metric['observation']['receipts'], 3)

    def test_source_release_lag_and_unreleased_target_are_separate(self):
        metric = analyze([event('send', 0, publish=600000000, scheduled=300000000)], config(), RULES)['left_image']
        self.assertEqual(metric['release_lag']['p99_ms'], 300)
        self.assertEqual(metric['missed_source_releases'], 59)

    def test_burst_expected_counts_use_batch_release_time(self):
        self.assertEqual(expected_releases(0, 1000000000, 8., 4), 8)
        self.assertEqual(expected_releases(250000000, 750000000, 8., 4), 4)

    def test_sparse_bulk_observation_uses_whole_burst_periods(self):
        settings = dict(PARAMETERS, bulk_bytes=8 * 1024**2, bulk_hz=.25, bulk_burst=4)
        result = analyze([], config(duration=48., benchmark_parameters=settings), RULES)
        observation = result['clock']['observation']
        self.assertEqual((observation['start_ns'], observation['stop_ns']),
                         (16000000000, 48000000000))
        self.assertEqual(observation['seconds'], 32)
        self.assertEqual(result, analyze([], config(duration=48., benchmark_parameters=settings),
            dict(RULES, bulk_observation_alignment='complete_periods')))

    def test_short_single_burst_fixed_window_keeps_samples_losses_and_empty_streams(self):
        settings = dict(PARAMETERS, bulk_bytes=8 * 1024**2, bulk_hz=5., bulk_burst=1)
        events = [event('send', 1, stream=stream, scheduled=200000000, publish=200000000,
                        size=8 * 1024**2 + 80)
                  for stream in ('bulk_image_h2g', 'bulk_image_g2h')]
        events.append(event('receive', 1, stream='bulk_image_h2g', scheduled=200000000,
                            publish=200000000, callback=220000000, size=8 * 1024**2 + 80))
        result = analyze(events, short_config(benchmark_parameters=settings), SHORT_RULES)
        metric = result['bulk_image_h2g']
        observed = metric['observation']
        self.assertEqual((observed['start_ns'], observed['stop_ns'], observed['seconds']),
                         (50000000, 250000000, .2))
        self.assertEqual(len(observed['bins']), 8)
        self.assertEqual(sum(row['receipts'] for row in observed['bins']), 1)
        self.assertEqual((metric['expected_scheduled_releases'], metric['published'],
                          metric['received'], observed['receipts']), (1, 1, 1, 1))
        self.assertEqual(metric['latency']['count'], 1)
        self.assertEqual(metric['latency']['p99_ms'], 20)
        self.assertEqual(metric['p99_tail_sample_count'], .01)
        self.assertEqual(metric['sustainability'], 'insufficient_repeated_window_evidence')
        lost = result['bulk_image_g2h']
        self.assertEqual(lost['censored_sequence_ids'], [1])
        self.assertEqual(lost['missing_age_deadline_misses'], 1)
        self.assertEqual(lost['observation']['max_outstanding_messages'], 1)
        for name in ('bulk_image_g2h', 'joint_states'):
            with self.subTest(stream=name):
                self.assertEqual(result[name]['latency']['count'], 0)
                self.assertIsNone(result[name]['latency']['p99_ms'])
                self.assertEqual(result[name]['observation']['receipts'], 0)
                self.assertFalse(result[name]['bounded_observation'])

    def test_short_bulk_default_still_requires_complete_burst_periods(self):
        settings = dict(PARAMETERS, bulk_bytes=8 * 1024**2, bulk_hz=5., bulk_burst=1)
        rules = dict(SHORT_RULES)
        del rules['bulk_observation_alignment']
        with self.assertRaisesRegex(ValueError, 'complete burst periods'):
            analyze([], short_config(benchmark_parameters=settings), rules)

    def test_unknown_bulk_alignment_rejected_even_without_bulk(self):
        for bulk_bytes in (0, 8 * 1024**2):
            for alignment in ('unknown', None):
                with self.subTest(bulk_bytes=bulk_bytes, alignment=alignment):
                    with self.assertRaisesRegex(ValueError, 'Unknown bulk observation alignment'):
                        analyze([], short_config(benchmark_parameters=dict(PARAMETERS,
                            bulk_bytes=bulk_bytes)), dict(SHORT_RULES,
                            bulk_observation_alignment=alignment))

    def test_one_short_window_cannot_establish_repeated_load_evidence(self):
        settings = short_config()
        row = dict(status='complete', config=settings, errors=[],
                   case=dict(implementation='direct_dds_m5ops', cell='short-ball', name='short'),
                   metrics=analyze([], settings, SHORT_RULES))
        assessments, _ = load_assessments([row], SHORT_RULES)
        self.assertEqual(assessments[0]['independent_windows'], 1)
        self.assertEqual(assessments[0]['status'], 'insufficient_repeated_evidence')

    def test_stereo_requires_corresponding_sequence_ids(self):
        events = [event('send', n, stream=eye) for n in (0, 1) for eye in ('left_image', 'right_image')]
        events += [event('receive', 0), event('receive', 1, stream='right_image')]
        pair = analyze(events, config(), RULES)['stereo_pair']
        self.assertEqual((pair['published'], pair['received'], pair['missing_at_drain']), (2, 0, 2))

    def test_stereo_last_callback_age_and_skew(self):
        events = [event('send', 0, stream=eye) for eye in ('left_image', 'right_image')]
        events += [event('receive', 0, callback=350000000),
                   event('receive', 0, stream='right_image', callback=375000000)]
        pair = analyze(events, config(), RULES)['stereo_pair']
        self.assertEqual(pair['latency']['p99_ms'], 75)
        self.assertEqual(pair['age']['p99_ms'], 75)
        self.assertEqual(pair['callback_skew']['p99_ms'], 25)

    def test_nearest_rank_percentile_is_not_mean_of_percentiles(self):
        result = statistics([1000000] * 99 + [100000000])
        self.assertEqual((result['p50_ms'], result['p95_ms'], result['p99_ms'], result['max_ms']),
                         (1, 1, 1, 100))


class EvidenceTests(unittest.TestCase):
    def test_final_pair_diagnostic_is_excluded_from_main_performance(self):
        row = dict(status='complete', metrics={'clock': {}}, errors=[], config=config(),
                   case=dict(implementation='initialized_m5'))
        compare([row], {})
        self.assertFalse(row['comparison_eligible'])
        self.assertIn('diagnostic', row['comparison_exclusion'])

    def test_pending_catch_gate_rejected_before_runtime_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'gate.json'
            write_json(path, dict(experiment='actual_ball_catching', cpu_type='kvm',
                                 status='pending', physical_host='ubuntu-george'))
            with self.assertRaisesRegex(ValueError, 'actual KVM'):
                check_gate(path, 'ubuntu-george')
    def test_staging_preserves_implementation_driver_and_guest_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, common = root / 'old', root / 'common'
            for prefix in (old, common):
                for relative in (*COMMON_FILES, *IMPLEMENTATION_FILES):
                    path = prefix / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((prefix.name + ':' + relative).encode())
                (prefix / 'lib/libchimaera_dds_transport.so').write_bytes(prefix.name.encode())
            (old / COMMON_FILES[1]).chmod(0o444)
            (common / 'lib/libnew_rmw_dependency.so').write_bytes(b'common dependency')
            original_bundle = root / 'guest.tar.gz'
            with tarfile.open(original_bundle, 'w:gz') as archive:
                for relative in (*COMMON_FILES, *IMPLEMENTATION_FILES, 'lib/libchimaera_dds_transport.so'):
                    archive.add(old / relative, arcname='opt/chimaera/latency/app/' + relative)
                for relative in (*COMMON_FILES[1:], 'lib/libchimaera_dds_transport.so'):
                    archive.add(old / relative, arcname='opt/chimaera/latency/' + relative)
            output = root / 'matched'
            (old / 'lib').chmod(0o555)
            try:
                stage(old, original_bundle, common, common / COMMON_FILES[0], output)
            finally:
                (old / 'lib').chmod(0o755)
            for relative in IMPLEMENTATION_FILES:
                self.assertEqual((output / 'runtime' / relative).read_bytes(), (old / relative).read_bytes())
            before = bundle_common(original_bundle, IMPLEMENTATION_FILES)
            after = bundle_common(output / 'guest.tar.gz', IMPLEMENTATION_FILES)
            self.assertEqual(before, after)
            self.assertNotIn('lib/chimaera_ros_bridge/latency_driver', COMMON_FILES)
            self.assertEqual((output / 'runtime/lib/libchimaera_dds_transport.so').read_bytes(), b'old')
            self.assertEqual((output / 'runtime/lib/libnew_rmw_dependency.so').read_bytes(), b'common dependency')
            with tarfile.open(output / 'guest.tar.gz') as archive:
                for relative in COMMON_FILES[1:]:
                    self.assertEqual(archive.extractfile('opt/chimaera/latency/' + relative).read(),
                                     (common / relative).read_bytes())
                for prefix in ('', 'app/'):
                    self.assertEqual(archive.extractfile('opt/chimaera/latency/' + prefix +
                        'lib/libchimaera_dds_transport.so').read(), b'old')
                    self.assertEqual(archive.extractfile('opt/chimaera/latency/' + prefix +
                        'lib/libnew_rmw_dependency.so').read(), b'common dependency')

    def test_csv_join_retains_raw_unset_but_uses_source_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.csv'
            rows = [event('send', 0), event('receive', 0)]
            rows[1]['publish_ns'] = UNSET
            with path.open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            result = read_events(path, config())
            self.assertEqual(result[1]['publish_ns'], 300000000)
            self.assertIn(str(UNSET), path.read_text())

    def test_orphan_and_corrupt_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.csv'
            for rows in ([event('receive', 0)],
                         [event('send', 0), event('receive', 0, size=1)],
                         [event('send', 0), event('receive', 0, callback=1)]):
                with path.open('w') as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                with self.assertRaises(ValueError):
                    read_events(path, config())

    def test_optional_publish_duration_is_validated_and_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.csv'
            rows = [event('send', 0), event('receive', 0)]
            rows[0]['publish_return_ns'] = rows[0]['publish_ns'] + 750000
            rows[1]['publish_return_ns'] = UNSET
            with path.open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            parsed = read_events(path, config())
            result = analyze(parsed, config(), RULES)
            self.assertEqual(result['left_image']['publish_call']['max_ms'], .75)
            rows[0]['publish_return_ns'] = rows[0]['publish_ns'] - 1
            with path.open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, 'Publish return'):
                read_events(path, config())

    def test_creation_latency_includes_preparation_and_excludes_release_lag(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.csv'
            rows = [event('send', 0), event('receive', 0)]
            rows[0]['scheduled_ns'] = rows[1]['scheduled_ns'] = 280000000
            rows[0]['creation_ns'] = 290000000
            rows[1]['creation_ns'] = UNSET
            def parse():
                with path.open('w') as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                return read_events(path, config())
            parsed = parse()
            self.assertEqual(parsed[1]['creation_ns'], 290000000)
            metric = analyze(parsed, config(), RULES)['left_image']
            self.assertEqual(metric['creation_to_callback']['max_ms'], 110)
            self.assertEqual(metric['preparation']['max_ms'], 10)
            self.assertEqual(metric['latency']['max_ms'], 100)
            self.assertEqual(metric['age']['max_ms'], 120)
            rows[1]['creation_ns'] = 291000000
            with self.assertRaisesRegex(ValueError, 'Receipt creation'):
                parse()
            rows[1]['creation_ns'] = UNSET
            for invalid in (279999999, 300000001):
                rows[0]['creation_ns'] = invalid
                with self.assertRaisesRegex(ValueError, 'Creation timestamp'):
                    parse()

    def test_historical_rows_do_not_invent_creation_timestamps(self):
        metric = analyze([event('send', 0), event('receive', 0)], config(), RULES)['left_image']
        self.assertEqual(metric['creation_to_callback']['count'], 0)
        self.assertIsNone(metric['creation_to_callback']['max_ms'])

    def test_configuration_tampering_rejected(self):
        validate_config(config())
        for change in (dict(single_boot=True), dict(num_cores=1), dict(interval_us=1000),
                       dict(ball_catching=dict(BALL, camera_width=640)), dict(verify_payload=0)):
            with self.assertRaises(ValueError):
                validate_config(config(**change))

    def test_dormant_cpu_name_does_not_establish_active_model(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            write_json(directory / 'gem5/latency_clock_origin.json',
                dict(active_cpus=['X86KvmCPU', 'X86KvmCPU'], cpu='timing', cpu_clock='3GHz'))
            with self.assertRaisesRegex(ValueError, 'Active CPU'):
                cpu_evidence(directory, config())

    def test_failed_incomplete_unrun_and_malformed_attempts_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cases = [dict(name=name) for name in ('failed', 'incomplete', 'not_run', 'malformed')]
            write_json(root / 'suite.json', dict(schema_version=1, matrix=dict(cases=cases)))
            write_json(root / 'failed/benchmark-attempt.json', dict(status='failed', error='boot failed'))
            (root / 'incomplete').mkdir()
            (root / 'malformed').mkdir()
            (root / 'malformed/benchmark-attempt.json').write_text('{truncated')
            _, result = collect(root)
            self.assertEqual([row['status'] for row in result], ['failed', 'incomplete', 'not_run', 'invalid'])
            self.assertIn('boot failed', result[0]['errors'])

    def test_stats_dumps_retained_without_double_counting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stats.txt'
            path.write_text('---------- Begin Simulation Statistics ----------\n'
                'simInsts 100\nboard.memory.mem_ctrl.bytesReadSys 300\n'
                '---------- End Simulation Statistics ----------\n'
                '---------- Begin Simulation Statistics ----------\nsimInsts 200\n')
            result = gem5_stats(path)
            self.assertEqual(len(result['completed_dumps']), 1)
            self.assertEqual(result['completed_dumps'][0]['simInsts'], 100)
            self.assertEqual(result['partial_final_dump']['simInsts'], 200)


class NativeReferenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.row = dict(status='complete', errors=[], config=short_config(),
            case=dict(name='short-ball-original_m5-chimaera-shm-r1', implementation='original_m5',
                      cell='short-ball', local_profile='shm'), path=str(root / 'measured'),
            metrics={'left_image': dict(published=6, missing_at_drain=0, duplicates=0, reordered=0)})
        self.reference = dict(status='complete', errors=[], config=short_config(mode='native_free'),
            case=dict(name='short-ball-shared-native-free-shm-r1', implementation='direct_dds_m5ops',
                      cell='short-ball', local_profile='shm'), path=str(root / 'reference'),
            metrics=deepcopy(self.row['metrics']))
        for row in (self.row, self.reference):
            path = Path(row['path'])
            path.mkdir()
            (path / 'dds.xml').write_text('<same-shm-profile/>')
        common = {name: f'{index:064x}' for index, name in enumerate(COMMON_FILES)}
        self.suite = dict(matrix=dict(native_references=dict(mode='shared_native_free_shm',
            by_cell={'short-ball': 'short-ball-shared-native-free-shm-r1',
                     'short-bulk8m': 'short-bulk8m-shared-native-free-shm-r1'})),
            lock=dict(variants={name: dict(common_files=dict(common))
                                for name in ('original_m5', 'direct_dds_m5ops')}))

    def test_shared_reference_matches_across_implementations_without_native_stepped(self):
        attempts = [self.row, self.reference]
        expected = dict(native_free=self.reference['case']['name'])
        self.assertEqual(native_matches(self.row, attempts, self.suite), expected)
        self.assertEqual(compare(attempts, self.suite), [])
        self.assertTrue(self.row['comparison_eligible'])
        self.assertEqual(self.row['native_references'], expected)

    def test_bulk_cell_selects_its_own_declared_reference(self):
        settings = dict(PARAMETERS, bulk_bytes=8 * 1024**2, bulk_hz=5., bulk_burst=1)
        for row in (self.row, self.reference):
            row['case']['cell'] = 'short-bulk8m'
            row['config']['benchmark_parameters'] = dict(settings)
        self.reference['case']['name'] = 'short-bulk8m-shared-native-free-shm-r1'
        self.assertEqual(native_matches(self.row, [self.reference], self.suite),
                         dict(native_free=self.reference['case']['name']))

    def test_exact_declared_case_is_used_despite_other_compatible_peers(self):
        other = deepcopy(self.reference)
        other['case'].update(name='other-compatible-reference', implementation='original_m5')
        self.assertEqual(native_matches(self.row, [other, self.reference], self.suite),
                         dict(native_free=self.reference['case']['name']))
        with self.assertRaisesRegex(ValueError, 'exactly one declared'):
            native_matches(self.row, [other], self.suite)

    def test_duplicate_declared_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one declared'):
            native_matches(self.row, [self.reference, deepcopy(self.reference)], self.suite)

    def test_missing_cell_declaration_is_rejected(self):
        del self.suite['matrix']['native_references']['by_cell']['short-ball']
        with self.assertRaisesRegex(ValueError, 'Missing declared'):
            native_matches(self.row, [self.reference], self.suite)

    def test_unknown_or_malformed_explicit_policy_never_falls_back(self):
        for policy in (None, {}, dict(mode='unknown', by_cell={}),
                       dict(mode='shared_native_free_shm', by_cell=[])):
            with self.subTest(policy=policy):
                self.suite['matrix']['native_references'] = policy
                with self.assertRaisesRegex(ValueError, 'native reference policy'):
                    native_matches(self.row, [self.reference], self.suite)

    def test_incomplete_or_errored_declared_reference_is_rejected(self):
        for change in (dict(status='failed'), dict(status='incomplete'), dict(status='not_run'),
                       dict(metrics=None), dict(metrics={}), dict(errors=['provenance missing']),
                       dict(errors=None)):
            with self.subTest(change=change):
                reference = deepcopy(self.reference)
                reference.update(change)
                with self.assertRaisesRegex(ValueError, 'complete metrics and no errors'):
                    native_matches(self.row, [reference], self.suite)

    def test_native_mode_local_profile_and_cell_must_match(self):
        for field, value in (('mode', 'native_stepped'), ('mode', 'chimaera'),
                             ('local_profile', 'udp'), ('cell', 'short-bulk8m')):
            with self.subTest(field=field, value=value):
                reference = deepcopy(self.reference)
                target = reference['config'] if field == 'mode' else reference['case']
                target[field] = value
                with self.assertRaisesRegex(ValueError, 'configuration differs'):
                    native_matches(self.row, [reference], self.suite)

    def test_source_delivery_and_dds_config_must_match(self):
        for change in (dict(repeat=2), dict(duration=.3), dict(warmup=.1), dict(drain=.2),
                       dict(reliability='best_effort'), dict(depth=64), dict(delivery_policy='latest'),
                       dict(verify_payload=True), dict(dds_tuning='other'), dict(dds_shm_bytes=1024),
                       dict(dds_max_message_bytes=1024),
                       dict(benchmark_parameters=dict(PARAMETERS, cameras_enabled=False))):
            with self.subTest(change=change):
                reference = deepcopy(self.reference)
                reference['config'].update(change)
                with self.assertRaisesRegex(ValueError, 'configuration differs'):
                    native_matches(self.row, [reference], self.suite)

    def test_dds_xml_must_exist_and_match_exactly(self):
        profile = Path(self.reference['path']) / 'dds.xml'
        profile.write_text('<different-shm-profile/>')
        with self.assertRaisesRegex(ValueError, 'DDS profile differs'):
            native_matches(self.row, [self.reference], self.suite)
        profile.unlink()
        with self.assertRaisesRegex(ValueError, 'DDS profile missing'):
            native_matches(self.row, [self.reference], self.suite)

    def test_every_common_hash_must_match_owner_and_measured_lock(self):
        for implementation in ('original_m5', 'direct_dds_m5ops'):
            for relative in COMMON_FILES:
                with self.subTest(implementation=implementation, relative=relative):
                    suite = deepcopy(self.suite)
                    suite['lock']['variants'][implementation]['common_files'][relative] = 'f' * 64
                    with self.assertRaisesRegex(ValueError, 'hashes missing or differ'):
                        native_matches(self.row, [self.reference], suite)

    def test_missing_common_hashes_cannot_match_by_absence(self):
        for missing in (COMMON_FILES[0], 'common_files', 'variant'):
            with self.subTest(missing=missing):
                suite = deepcopy(self.suite)
                for variant in suite['lock']['variants'].values():
                    if missing == 'common_files':
                        del variant['common_files']
                    elif missing != 'variant':
                        del variant['common_files'][missing]
                if missing == 'variant':
                    del suite['lock']['variants']['direct_dds_m5ops']
                with self.assertRaisesRegex(ValueError, 'hashes missing or differ'):
                    native_matches(self.row, [self.reference], suite)

    def test_empty_or_lossy_native_stream_is_still_rejected(self):
        for change in (dict(published=0), dict(missing_at_drain=1), dict(duplicates=1),
                       dict(reordered=1)):
            with self.subTest(change=change):
                reference = deepcopy(self.reference)
                reference['metrics']['left_image'].update(change)
                with self.assertRaisesRegex(ValueError, 'Unusable native delivery reference'):
                    native_matches(self.row, [reference], self.suite)

    def test_absent_policy_keeps_both_per_implementation_native_modes_required(self):
        del self.suite['matrix']['native_references']
        with self.assertRaisesRegex(ValueError, 'matched SHM native_free'):
            native_matches(self.row, [self.reference], self.suite)
        self.reference['case']['implementation'] = 'original_m5'
        with self.assertRaisesRegex(ValueError, 'matched SHM native_stepped'):
            native_matches(self.row, [self.reference], self.suite)
        stepped = deepcopy(self.reference)
        stepped['case']['name'] = 'historical-native-stepped'
        stepped['config']['mode'] = 'native_stepped'
        self.assertEqual(native_matches(self.row, [self.reference, stepped], self.suite),
                         dict(native_free=self.reference['case']['name'],
                              native_stepped=stepped['case']['name']))


class TailTests(unittest.TestCase):
    def rows(self, count=5, candidate_p99=8, candidate_misses=0):
        return [dict(baseline='scheduled_dds_dma', cell='ball', policy='lossless',
            stream='effort_command', delivery_valid=True, equal_source_count=True,
            baseline_p99_ms=10., candidate_p99_ms=candidate_p99,
            baseline_receipts=2000, candidate_receipts=2000,
            baseline_bounded=True, candidate_bounded=True,
            baseline_deadline_misses=0, candidate_deadline_misses=candidate_misses)
            for _ in range(count)]

    def test_short_windows_cannot_pass_tail_gate(self):
        result = tail_decisions(self.rows(3), RULES)[0]
        self.assertEqual(result['decision'], 'insufficient_evidence')
        self.assertIsNone(result['mean_paired_window_p99_delta_upper_95_ms'])

    def test_one_short_window_has_no_tail_confidence_bound(self):
        rows = self.rows(1)
        rows[0].update(baseline_receipts=50, candidate_receipts=50)
        result = tail_decisions(rows, SHORT_RULES)[0]
        self.assertEqual(result['independent_windows'], 1)
        self.assertFalse(result['adequate_counts'])
        self.assertIsNone(result['mean_paired_window_p99_delta_upper_95_ms'])
        self.assertEqual(result['decision'], 'insufficient_evidence')

    def test_mean_p99_improvement_cannot_hide_added_deadline_misses(self):
        result = tail_decisions(self.rows(candidate_misses=1), RULES)[0]
        self.assertEqual(result['decision'], 'fail_observed_nonregression')

    def test_paired_bootstrap_bound_with_adequate_identical_differences(self):
        result = tail_decisions(self.rows(), RULES)[0]
        self.assertEqual(result['mean_paired_window_p99_delta_upper_95_ms'], -2.)
        self.assertEqual(result['decision'], 'pass_observed_nonregression')

    def test_failed_planned_pair_prevents_survivor_only_tail_pass(self):
        rows = self.rows()
        for row in rows:
            row['cohort_complete'] = False
        self.assertEqual(tail_decisions(rows, RULES)[0]['decision'], 'incomplete_cohort')


if __name__ == '__main__':
    unittest.main()
