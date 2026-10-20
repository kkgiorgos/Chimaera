import json
from pathlib import Path
import tempfile
import unittest

from collect_integrated import collect, export


class Attempts(unittest.TestCase):
    def test_diagnostic_history_cannot_retain_a_stale_matched_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'matrix'
            case = root / 'failed'
            case.mkdir(parents=True)
            (case / 'run.json').write_text(json.dumps({'status': 'failed'}))
            output = Path(temporary) / 'export'
            output.mkdir()
            (output / 'comparison.csv').write_text('stale matched measurements\n')
            self.assertEqual(export([root], output, 'machine', history_only=True), (1, 0, 0))
            self.assertFalse((output / 'comparison.csv').exists())
            history = json.loads((output / 'attempts.json').read_text())
            self.assertFalse(history['comparisons_validated'])
            self.assertEqual(history['attempts'][0]['result'], 'failed')

    def test_failed_and_cancelled_attempts_remain_visible_without_becoming_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'matrix'
            for status in ('failed', 'cancelled', 'complete'):
                case = root / status
                case.mkdir(parents=True)
                (case / 'run.json').write_text(json.dumps({'status': status}))
                (case / 'profile-provenance.json').write_text(json.dumps({'result': status}))
                # Even a stale summary cannot turn an explicitly failed case into success.
                (case / 'summary.json').write_text(json.dumps(dict(config={}, directions={})))
            (root / 'complete/dds-overlay.json').write_text(json.dumps(
                dict(tuning='large_shm_only', max_message_bytes=4194304)))
            attempts, measurements = collect([root])
            self.assertEqual({a['result'] for a in attempts}, {'failed', 'cancelled', 'complete'})
            self.assertEqual([m['path'] for m in measurements], ['matrix/complete'])
            self.assertEqual(next(a for a in attempts if a['result'] == 'complete')
                             ['dds-overlay.json']['max_message_bytes'], 4194304)


if __name__ == '__main__':
    unittest.main()
