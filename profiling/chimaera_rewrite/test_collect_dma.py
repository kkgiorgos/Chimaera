import json
from pathlib import Path
import tempfile
import unittest

from collect_dma import collect


class NativeDmaReference(unittest.TestCase):
    def test_completed_guest_requires_matching_native_and_cpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / 'dma-test'
            (case / 'gem5').mkdir(parents=True)
            def write(name, value):
                (case / name).write_text(json.dumps(value))
            write('provenance.json', dict(result='complete', cpu='timing'))
            result = dict(integrity='pass', reset_reopen='pass',
                          samples=[dict(bytes=64, repeat=0, verified=True)])
            write('gem5/chimaera-dma-probe.json', result)
            with self.assertRaisesRegex(ValueError, 'native'):
                collect(root)
            write('native.json', dict(result, samples=[]))
            with self.assertRaisesRegex(ValueError, 'unmatched'):
                collect(root)
            write('native.json', result)
            with self.assertRaisesRegex(ValueError, 'CPU'):
                collect(root)
            write('gem5/dma_probe_origin.json', dict(active_cpus=['BaseTimingSimpleCPU'] * 2))
            self.assertEqual(len(collect(root)), 1)

    def test_failed_attempt_remains_visible_without_guest_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / 'dma-failed'
            case.mkdir()
            (case / 'provenance.json').write_text(json.dumps(dict(result='failed', error='deadlock')))
            records = collect(Path(temporary))
            self.assertEqual(records[0]['provenance']['error'], 'deadlock')
            self.assertIsNone(records[0]['guest'])
