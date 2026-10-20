import json
from pathlib import Path
import tempfile
import unittest

from collect_integrated import collect


class Attempts(unittest.TestCase):
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
