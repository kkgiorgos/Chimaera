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
        self.assertEqual(a['delivery']['latency']['mean_ms'], 4.)
        self.assertEqual(b['delivery']['latency']['p99_ms'], 10.)
        self.assertIsNone(a['latency'])  # Old takes cannot recover transport arrival.
        self.assertEqual(a['transport']['status'], 'not_recorded')
        self.assertEqual((a['delivery']['received'], b['delivery']['received']), (1, 1))
        self.assertEqual(a['boundary'], 'transport')

    def test_source_window_and_evaluation_censoring(self):
        self.write([self.event('publish', 'warmup', 1),
                    self.event('take', 'warmup', 1500000),
                    self.event('publish', 'late', 1200000),
                    self.event('take', 'late', 3000000),
                    self.event('publish', 'received', 1300000),
                    self.event('take', 'received', 1400000)])
        stream = self.summary(start=.001, end=.002)['streams'][0]
        self.assertEqual(stream['delivery']['published'], 2)
        self.assertEqual(stream['delivery']['missing_at_evaluation'], 1)
        self.assertEqual(stream['delivery']['received'], 1)
        self.assertEqual(stream['delivery']['latency']['mean_ms'], .1)

    def test_duplicates_missing_and_orphans(self):
        self.write([self.event('publish', 'a', 1), self.event('take', 'a', 4),
                    self.event('take', 'a', 5), self.event('publish', 'b', 2),
                    self.event('take', 'unknown', 6)])
        stream = self.summary()['streams'][0]['delivery']
        self.assertEqual(stream['duplicates'], 1)
        self.assertEqual(stream['received'], 1)
        self.assertEqual(stream['missing_at_evaluation'], 1)
        self.assertEqual(stream['unmatched_receipt_identities'], 1)
        self.assertEqual(stream['status'], 'partial')

    def test_invalid_clock_is_never_clamped(self):
        self.write([self.event('publish', 'negative', 100), self.event('take', 'negative', 50),
                    self.event('publish', 'unset', None), self.event('take', 'unset', 40)])
        stream = self.summary()['streams'][0]['delivery']
        self.assertEqual(stream['negative_latency'], 1)
        self.assertEqual(stream['invalid_clock_events'], 1)
        self.assertIsNone(stream['latency'])
        self.assertEqual(stream['status'], 'invalid_clock')

    def test_bridge_ignores_local_rmw_hops(self):
        self.write([self.event('publish', 'local', 1), self.event('take', 'local', 2, side='host'),
                    self.event('publish', 'crossing', 10, boundary='bridge'),
                    self.event('take', 'crossing', 20, boundary='bridge')])
        stream = self.summary()['streams'][0]
        self.assertEqual(stream['delivery']['boundary'], 'bridge')
        self.assertEqual(stream['delivery']['published'], 1)
        self.assertEqual(stream['delivery']['received'], 1)

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
        self.assertEqual(stream['delivery']['latency']['samples'], 5000)

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

    def packet(self, event, identity, ns, *, first=1, count=1, total=1, process=None, receiver=None):
        side = 'host' if event == 'send' else 'guest'
        process = process or side
        return dict(version=2, boundary='transport', event=event, id=identity, topic=None,
                    side=side, process=process, receiver=receiver or process + ':1', time_ns=ns,
                    fragment_first=first, fragment_count=count, fragment_total=total)

    def test_transport_excludes_serialization_and_delayed_executor_take(self):
        rows = [self.event('publish', 'a', 1000000), self.packet('send', 'a', 2000000),
                self.packet('receive', 'a', 3000000)]
        for taken in (40000000, 60000000):
            self.write(rows + [dict(self.event('take', 'a', taken), version=2, process='guest')])
            stream = self.summary()['streams'][0]
            self.assertEqual(stream['latency']['mean_ms'], 1.)
            self.assertEqual(stream['delivery']['latency']['mean_ms'], taken / 1e6 - 1.)
            self.assertEqual(stream['serialization_and_send']['mean_ms'], 1.)
            self.assertEqual(stream['receive_to_take']['mean_ms'], taken / 1e6 - 3.)

    def test_arrival_survives_a_qos_drop_before_take(self):
        self.write([self.event('publish', 'a', 1), self.packet('send', 'a', 2),
                    self.packet('receive', 'a', 3)])
        stream = self.summary()['streams'][0]
        self.assertEqual(stream['transport']['received'], 1)
        self.assertEqual(stream['delivery']['received'], 0)
        self.assertEqual(stream['delivery']['missing_at_evaluation'], 1)
        self.assertIsNone(stream['receive_to_take'])

    def test_fragments_require_complete_arrival_and_allow_retransmission(self):
        self.write([self.event('publish', 'a', 1),
                    self.packet('send', 'a', 2, total=2),
                    self.packet('send', 'a', 3, first=2, total=2),
                    self.packet('send', 'a', 4, first=2, total=2),
                    self.packet('receive', 'a', 5, first=2, total=2),
                    self.packet('receive', 'a', 6, first=2, total=2),
                    self.packet('receive', 'a', 7, total=2)])
        stream = self.summary()['streams'][0]['transport']
        self.assertEqual(stream['received'], 1)
        self.assertEqual(stream['duplicate_fragments'], 1)
        self.assertEqual(stream['latency']['mean_ms'], 5e-6)

    def test_fragment_fanout_never_combines_incomplete_receivers(self):
        self.write([self.event('publish', 'a', 1), self.packet('send', 'a', 2, count=2, total=2),
                    self.packet('receive', 'a', 3, total=2, receiver='guest:1'),
                    self.packet('receive', 'a', 4, first=2, total=2, receiver='guest:2')])
        stream = self.summary()['streams'][0]['transport']
        self.assertEqual(stream['received'], 0)
        self.assertEqual(stream['missing_at_evaluation'], 1)
        self.assertIsNone(stream['latency'])

    def test_waiting_matches_the_process_that_actually_takes(self):
        self.write([self.event('publish', 'a', 0), self.packet('send', 'a', 1000000),
                    self.packet('receive', 'a', 2000000, process='first'),
                    self.packet('receive', 'a', 3000000, process='second'),
                    dict(self.event('take', 'a', 5000000), version=2, process='second')])
        stream = self.summary()['streams'][0]
        self.assertEqual(stream['receive_to_take']['mean_ms'], 2.)

    def test_transport_clock_and_evaluation_censoring(self):
        rows = [self.event('publish', 'a', 1000000), self.packet('send', 'a', 1500000)]
        self.write(rows + [self.packet('receive', 'a', 3000000)])
        stream = self.summary(start=.001, end=.002)['streams'][0]['transport']
        self.assertEqual(stream['missing_at_evaluation'], 1)
        self.assertIsNone(stream['latency'])
        self.write(rows + [self.packet('receive', 'a', 500000)])
        result = self.summary()
        self.assertEqual(result['status'], 'invalid_clock')
        self.assertEqual(result['streams'][0]['transport']['negative_latency'], 1)
        self.assertIsNone(result['streams'][0]['latency'])

    def test_invalid_fragment_metadata_and_mixed_totals_fail(self):
        self.write([self.event('publish', 'a', 1), self.packet('send', 'a', 2, total=2),
                    self.packet('receive', 'a', 3, total=3)])
        with self.assertRaisesRegex(ValueError, 'fragment totals'):
            self.summary()
        self.write([self.packet('send', 'a', 2, first=0)])
        with self.assertRaisesRegex(ValueError, 'trace record'):
            self.summary()

    def test_source_window_is_independent_of_receipt_drain(self):
        self.write([self.event('publish', 'a', 1000000), self.packet('send', 'a', 1200000),
                    self.packet('receive', 'a', 2500000), self.event('publish', 'drain', 2200000),
                    self.packet('send', 'drain', 2300000), self.packet('receive', 'drain', 2400000)])
        stream = self.summary(start=.001, source_end=.002, end=.003)['streams'][0]
        self.assertEqual(stream['published'], 1)
        self.assertEqual(stream['transport']['published'], 1)
        self.assertEqual(stream['transport']['received'], 1)
        self.assertEqual(stream['latency']['mean_ms'], 1.3)
        with self.assertRaisesRegex(ValueError, 'source window'):
            self.summary(start=.001, source_end=.004, end=.003)


if __name__ == '__main__':
    unittest.main()
