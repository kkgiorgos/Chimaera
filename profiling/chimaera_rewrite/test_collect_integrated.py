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
            attempts, measurements = collect([root])
            self.assertEqual({a['result'] for a in attempts}, {'failed', 'cancelled', 'complete'})
            self.assertEqual([m['path'] for m in measurements], ['matrix/complete'])


if __name__ == '__main__':
    unittest.main()
