import csv
import json
from pathlib import Path
import tempfile
import unittest

from analyze_pipeline import analyze


class PipelineAnalysisTests(unittest.TestCase):
    def make_case(self, root):
        case = Path(root) / 'native'
        case.mkdir()
        (case / 'run.json').write_text(json.dumps(dict(mode='native_free', cpu='native',
            warmup=0, duration=1, interval_us=2000, verify_payload=True)))
        (case / 'summary.json').write_text(json.dumps(dict(streams=dict(left_image=dict(
            published=1, received=1, missing_at_drain=0, duplicates=0, reordered=0)))))
        fields = ['pid', 'stage', 'stream', 'capture_ns', 'bytes', 'sim_ns',
                  'wall_ns', 'thread_cpu_ns']
        source = ['publish_begin', 'serialize_begin', 'serialize_end', 'publish_end']
        consumer = ['deserialize_begin', 'deserialize_end', 'callback_begin', 'verify_end']
        for pid, stages, offset in ((1, source, 0), (2, consumer, 400)):
            with (case / f'pipeline-{pid}.csv').open('w') as handle:
                writer = csv.DictWriter(handle, fields)
                writer.writeheader()
                for index, stage in enumerate(stages):
                    value = offset + index * 100
                    writer.writerow(dict(pid=pid, stage=stage, stream=1, capture_ns=0,
                        bytes=518400, sim_ns=value, wall_ns=value + 1000,
                        thread_cpu_ns=value))
        with (case / 'events.csv').open('w') as handle:
            writer = csv.DictWriter(handle, ['side', 'event', 'stream', 'scheduled_ns',
                                             'callback_ns', 'publish_ns'])
            writer.writeheader()
            writer.writerow(dict(side='guest', event='receive', stream='left_image',
                                 scheduled_ns=0, callback_ns=550))
            writer.writerow(dict(side='host', event='send', stream='left_image',
                                 scheduled_ns=0, publish_ns=0))
        return case

    def test_partition_uses_actual_callback_and_reconstructs_latency(self):
        with tempfile.TemporaryDirectory() as root:
            result = analyze(self.make_case(root))
            self.assertEqual(result['errors'], [])
            sample, = result['samples']
            self.assertEqual(sample['total_ns'], 550)
            self.assertEqual(sum(sample['stages_ns'].values()), 550)
            self.assertEqual(sample['callback_recording_offset_ns'], 50)

    def test_duplicate_stage_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            case = self.make_case(root)
            path = case / 'pipeline-1.csv'
            with path.open('a') as handle:
                handle.write(path.read_text().splitlines()[1] + '\n')
            result = analyze(case)
            self.assertFalse(result['samples'])
            self.assertEqual(result['errors'][0]['duplicate_stages'], {'publish_begin': 2})

    def test_missing_stage_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            case = self.make_case(root)
            path = case / 'pipeline-2.csv'
            path.write_text('\n'.join(line for line in path.read_text().splitlines()
                                      if ',deserialize_begin,' not in line) + '\n')
            result = analyze(case)
            self.assertFalse(result['samples'])
            self.assertEqual(result['errors'][0]['missing'], ['deserialize_begin'])

    def test_negative_interval_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            case = self.make_case(root)
            path = case / 'pipeline-2.csv'
            path.write_text(path.read_text().replace(',500,1500,500', ',700,1700,700'))
            result = analyze(case)
            self.assertFalse(result['samples'])
            self.assertLess(result['errors'][0]['negative_ns']['rclcpp_to_callback'], 0)


if __name__ == '__main__':
    unittest.main()
