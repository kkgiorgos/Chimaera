import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from compare_implementations import acceptance_runs, export, rows_for, throughput


ROOT = Path(__file__).resolve().parents[2]
PILOTS = ROOT / 'docs/measurements/chimaera-rewrite-2026-10-20/runs.json'
ACCEPTANCE = ROOT / 'docs/measurements/chimaera-dma-integration-2026-10-20/acceptance/attempts.json'


class Comparison(unittest.TestCase):
    def test_losses_use_receipts_and_source_duration_including_drain(self):
        result = throughput(dict(published=6, received=5), .2, 921600)
        self.assertEqual(result['offered_per_source_second'], 30)
        self.assertEqual(result['delivered_per_source_second'], 25)
        self.assertAlmostEqual(result['payload_MB_per_source_second'], 23.04)
        self.assertAlmostEqual(result['delivery_fraction'], 5 / 6)

    def test_invalid_or_duplicate_counts_cannot_inflate_throughput(self):
        for metric in (dict(published=0, received=0), dict(published=6, received=7),
                       dict(published=6, received=-1),
                       dict(published=6, received=6, duplicates=1)):
            with self.subTest(metric=metric), self.assertRaises(ValueError):
                throughput(metric, 2, 64)
        for duration in (0, -1, float('nan'), float('inf')):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                throughput(dict(published=1, received=1), duration, 64)

    def test_unknown_body_size_does_not_become_zero_goodput(self):
        result = throughput(dict(published=500, received=446), 2, None)
        self.assertEqual(result['delivered_per_source_second'], 223)
        self.assertIsNone(result['payload_MB_per_source_second'])

    def test_stereo_pair_counts_both_images_once(self):
        runs = acceptance_runs(json.loads(ACCEPTANCE.read_text()))
        rows = rows_for(runs, 'acceptance')
        pair = next(r for r in rows if r['stream'] == 'stereo_pair')
        self.assertEqual(pair['delivered_per_source_second'], 30)
        self.assertEqual(pair['payload_body_bytes'], 1843200)
        self.assertAlmostEqual(pair['payload_MB_per_source_second'], 55.296)
        eyes = [r for r in rows if r['case'] == pair['case']
                and r['stream'] in ('left_image', 'right_image')]
        self.assertAlmostEqual(sum(r['payload_MB_per_source_second'] for r in eyes),
                               pair['payload_MB_per_source_second'])

    def test_history_and_missing_native_or_detailed_cpu_are_rejected(self):
        record = json.loads(ACCEPTANCE.read_text())
        invalid = copy.deepcopy(record)
        invalid['comparisons_validated'] = False
        with self.assertRaisesRegex(ValueError, 'diagnostic history'):
            acceptance_runs(invalid)
        invalid = copy.deepcopy(record)
        invalid['attempts'] = [a for a in invalid['attempts']
                               if not a['case'].endswith('control_2s_native_free')]
        with self.assertRaisesRegex(ValueError, 'missing matched native_free'):
            acceptance_runs(invalid)
        invalid = copy.deepcopy(record)
        invalid['attempts'][0]['cpu_origin']['active_cpus'] = ['X86KvmCPU'] * 2
        with self.assertRaisesRegex(ValueError, 'detailed CPU provenance'):
            acceptance_runs(invalid)

    def test_cross_backend_settings_cannot_change_behind_matched_native(self):
        record = json.loads(ACCEPTANCE.read_text())
        # Change all three modes together: native matching still passes, but
        # comparing the two backends would now confound a DDS change.
        for attempt in record['attempts']:
            if attempt['summary']['config']['transport_backend'] == 'dma':
                attempt['summary']['config']['dds_shm_bytes'] = 4 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, 'M5/DMA acceptance settings differ'):
            acceptance_runs(record)

    def test_export_preserves_cohorts_native_rows_and_csv_units(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            data = export(PILOTS, ACCEPTANCE, output)
            self.assertEqual(data['cases'], dict(historical=18, acceptance=18))
            self.assertEqual(len(data['rows']), 147)
            with (output / 'comparison.csv').open() as stream:
                csv_rows = list(csv.DictReader(stream))
            self.assertEqual(len(csv_rows), 147)
            original = next(r for r in csv_rows if r['cohort'] == 'historical'
                            and r['implementation'] == 'baseline'
                            and r['mode'] == 'chimaera' and r['stream'] == 'right_image')
            self.assertEqual(float(original['delivered_per_source_second']), 25)
            self.assertEqual(float(original['payload_MB_per_source_second']), 23.04)
            self.assertNotIn(b'\r', (output / 'comparison.csv').read_bytes())
            self.assertNotIn('const data=DATA;', (output / 'report.html').read_text())


if __name__ == '__main__':
    unittest.main()
