import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('native_baseline', ROOT/'middleware/native_baseline.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def fixtures():
    reference = json.loads((ROOT/'docs/measurements/chimaera-dds-middleware-2026-10-21/attempts.json').read_text())
    attempts = copy.deepcopy(list(baseline.native_cases(reference).values()))
    for attempt in attempts:
        config = attempt['summary']['config']
        config.update(dds_tuning='udp_loopback', dds_shm_bytes=0, dds_max_message_bytes=65500)
        config['artifact_sha256']['dds.xml'] = hashlib.sha256(baseline.udp_loopback_profile().encode()).hexdigest()
    return reference, dict(physical_host=reference['physical_host'], attempts=attempts)


class NativeBaselineTest(unittest.TestCase):
    def test_explicit_profile_has_only_loopback_udp_and_disables_data_sharing(self):
        root = ET.fromstring(baseline.udp_loopback_profile())
        ns = {'d': 'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
        self.assertEqual([e.text for e in root.findall('.//d:type', ns)], ['UDPv4'])
        self.assertEqual([e.text for e in root.findall('.//d:address', ns)], ['127.0.0.1'])
        self.assertEqual(root.find('.//d:useBuiltinTransports', ns).text, 'false')
        self.assertEqual([e.text for e in root.findall('.//d:data_sharing/d:kind', ns)], ['OFF', 'OFF'])

    def test_compares_both_native_modes_without_relaxing_other_matching_fields(self):
        reference, record = fixtures()
        rows = baseline.compare(reference, record)['rows']
        self.assertEqual(len(rows), 16)
        self.assertTrue(all(row['delivery_valid'] and row['equal_source_count'] for row in rows))
        self.assertEqual({row['mode'] for row in rows}, {'native_free', 'native_stepped'})
        config = record['attempts'][0]['summary']['config']
        config['duration'] = 2.
        with self.assertRaisesRegex(ValueError, 'settings differ'):
            baseline.compare(reference, record)

    def test_wrong_host_runtime_or_xml_is_rejected(self):
        for change, error in [('host', 'Physical host'), ('runtime', 'fingerprints'), ('xml', 'disable shared memory')]:
            with self.subTest(change=change):
                reference, record = fixtures()
                if change == 'host': record['physical_host'] = 'other'
                else:
                    hashes = record['attempts'][0]['summary']['config']['artifact_sha256']
                    key = 'dds.xml' if change == 'xml' else next(k for k in hashes if k.endswith('/latency_endpoint'))
                    hashes[key] = 'changed'
                with self.assertRaisesRegex(ValueError, error):
                    baseline.compare(reference, record)

    def test_missing_delivery_withholds_ratios_and_differences(self):
        reference, record = fixtures()
        summary = record['attempts'][0]['summary']
        metric = next(iter((summary.get('streams') or summary['directions']).values()))
        metric.update(received=metric['published']-1, missing_at_drain=1)
        selected = [row for row in baseline.compare(reference, record)['rows']
                    if row['workload'] == summary['config']['workload'] and row['mode'] == summary['config']['mode']]
        self.assertTrue(all(not row['delivery_valid'] and row['mean_increase_ms'] is None and
                            row['udp_to_shm_mean_ratio'] is None for row in selected))


if __name__ == '__main__':
    unittest.main()
