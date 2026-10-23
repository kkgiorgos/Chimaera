import json
from pathlib import Path
import tempfile
import unittest

from chimaera_ros.tracing import restore_prefix, summarize


class TraceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = dict(topics=[dict(host_topic='/camera', guest_topic='/image',
                                       direction='host_to_guest'),
                                  dict(host_topic='/effort', guest_topic='/command',
                                       direction='guest_to_host')])

    def write(self, rows, name='host'):
        path = self.root / f'chimaera-trace-{name}.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return path

    def event(self, event, id, ns, topic='/camera', side=None, boundary='rmw'):
        return dict(version=1, event=event, id=id, time_ns=ns, topic=topic,
                    side=side or ('host' if event == 'publish' else 'guest'), boundary=boundary)

    def summary(self, **kwargs):
        return summarize(self.root, self.routes, **kwargs)

    def test_missing_trace_is_not_zero(self):
        self.assertEqual(self.summary()['status'], 'not_recorded')
        self.assertEqual(self.summary()['streams'], [])

    def test_join_aliases_and_keep_streams_separate(self):
        self.write([self.event('publish', 'a', 1000000),
                    self.event('take', 'a', 5000000, topic='/image'),
                    self.event('publish', 'b', 2000000, topic='/command', side='guest'),
                    self.event('take', 'b', 12000000, topic='/effort', side='host')])
        a, b = self.summary()['streams']
        self.assertEqual(a['latency']['mean_ms'], 4.)
        self.assertEqual(b['latency']['p99_ms'], 10.)
        self.assertEqual((a['received'], b['received']), (1, 1))

    def test_source_window_and_evaluation_censoring(self):
        self.write([self.event('publish', 'warmup', 1),
                    self.event('take', 'warmup', 1500000),
                    self.event('publish', 'late', 1200000),
                    self.event('take', 'late', 3000000),
                    self.event('publish', 'received', 1300000),
                    self.event('take', 'received', 1400000)])
        stream = self.summary(start=.001, end=.002)['streams'][0]
        self.assertEqual(stream['published'], 2)
        self.assertEqual(stream['missing_at_evaluation'], 1)
        self.assertEqual(stream['received'], 1)
        self.assertEqual(stream['latency']['mean_ms'], .1)

    def test_duplicates_missing_and_orphans(self):
        self.write([self.event('publish', 'a', 1), self.event('take', 'a', 4),
                    self.event('take', 'a', 5), self.event('publish', 'b', 2),
                    self.event('take', 'unknown', 6)])
        stream = self.summary()['streams'][0]
        self.assertEqual(stream['duplicates'], 1)
        self.assertEqual(stream['received'], 1)
        self.assertEqual(stream['missing_at_evaluation'], 1)
        self.assertEqual(stream['unmatched_receipt_identities'], 1)
        self.assertEqual(stream['status'], 'partial')

    def test_invalid_clock_is_never_clamped(self):
        self.write([self.event('publish', 'negative', 100), self.event('take', 'negative', 50),
                    self.event('publish', 'unset', None), self.event('take', 'unset', 40)])
        stream = self.summary()['streams'][0]
        self.assertEqual(stream['negative_latency'], 1)
        self.assertEqual(stream['invalid_clock_events'], 1)
        self.assertIsNone(stream['latency'])
        self.assertEqual(stream['status'], 'invalid_clock')

    def test_bridge_ignores_local_rmw_hops(self):
        self.write([self.event('publish', 'local', 1), self.event('take', 'local', 2, side='host'),
                    self.event('publish', 'crossing', 10, boundary='bridge'),
                    self.event('take', 'crossing', 20, boundary='bridge')])
        stream = self.summary()['streams'][0]
        self.assertEqual(stream['boundary'], 'bridge')
        self.assertEqual(stream['published'], 1)
        self.assertEqual(stream['received'], 1)

    def test_malformed_and_duplicate_sources_fail(self):
        path = self.write([self.event('publish', 'a', 1)] * 2)
        with self.assertRaisesRegex(ValueError, 'Duplicate source'):
            self.summary()
        path.write_text('{"version":1')
        with self.assertRaisesRegex(ValueError, 'truncated'):
            self.summary()

    def test_plot_bounded_without_downsampling_statistics(self):
        rows = [row for i in range(5000) for row in
                (self.event('publish', str(i), i), self.event('take', str(i), i + 100))]
        self.write(rows)
        stream = self.summary()['streams'][0]
        self.assertLessEqual(len(stream['time_trace']), 2000)
        self.assertEqual(stream['latency']['samples'], 5000)

    def test_checkpoint_guest_prefix_preserves_export_offsets(self):
        checkpoint = self.root / 'checkpoint'
        (checkpoint / 'gem5').mkdir(parents=True)
        prefix = checkpoint / 'gem5/chimaera-trace-guest-10.jsonl'
        prefix.write_text('original prefix\n')
        destination = self.root / 'restored'
        restore_prefix(checkpoint, destination)
        self.assertEqual((destination / 'gem5' / prefix.name).read_bytes(), prefix.read_bytes())

    def test_unknown_topics_do_not_enter_statistics(self):
        self.write([self.event('publish', 'a', 1, topic='/unselected')])
        self.assertEqual(self.summary()['status'], 'no_publications')
        self.assertTrue(all(s['latency'] is None for s in self.summary()['streams']))

    def test_invalid_windows_fail(self):
        for start, end in [(-1, 2), (3, 2), (float('nan'), None), (0, float('inf'))]:
            with self.assertRaises(ValueError):
                self.summary(start=start, end=end)


if __name__ == '__main__':
    unittest.main()
