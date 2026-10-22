import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from chimaera_ros.latency import routes
from chimaera_ros.latency_config import DEFAULT, plan_sweep
from chimaera_ros.latency_session import validate_bundle
from chimaera_ros.manifest import load
from chimaera_ros.runner import plan


class DirectM5ConfigurationTest(unittest.TestCase):
    def test_bulk_arrays_do_not_enter_the_small_control_pool(self):
        topics = routes(config=dict(transport_backend='m5ops'))['topics']
        arrays = [topic for topic in topics if topic['type'] == 'std_msgs/msg/UInt8MultiArray']
        self.assertTrue(all(topic['transport_class'] == 'bulk' for topic in arrays))
        self.assertNotIn('transport_class', topics[-1])
        self.assertTrue(all('transport_class' not in topic for topic in routes()['topics']))

    def test_direct_mode_rejects_reuse_and_requires_matching_middleware(self):
        with self.assertRaisesRegex(ValueError, 'fresh boots'):
            plan_sweep(dict(DEFAULT, transport_backend='m5ops', middleware_transport='fastdds'))
        with self.assertRaisesRegex(ValueError, 'Fast DDS'):
            plan_sweep(dict(DEFAULT, transport_backend='m5ops', single_boot=False))

    def test_session_propagates_backend_and_notification_module_to_both_sides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'routes.json').write_text(json.dumps(dict(version=1, topics=[{}])))
            spec = dict(version=1, routes='routes.json',
                host=dict(domain_id=41, setup=[], processes=[]),
                guest=dict(domain_id=41, setup=[], processes=[]),
                bridge=dict(transport_backend='m5ops', middleware_transport='fastdds',
                    m5_module='/opt/chimaera/chimaera_m5.ko', m5_device='/dev/custom-m5'))
            path = root / 'session.json'
            path.write_text(json.dumps(spec))
            session = load(path)
            for side in ('host', 'guest'):
                planned = plan(session, side, '/opt/bridge')
                self.assertEqual(planned['env']['CHIMAERA_DDS_BACKEND'], 'm5ops')
                self.assertEqual(planned['env']['CHIMAERA_M5_DEVICE'], '/dev/custom-m5')
            guest = plan(session, 'guest', '/opt/bridge')['processes'][0]['command']
            self.assertIn('m5_module:=/opt/chimaera/chimaera_m5.ko', guest)
            self.assertFalse(any(value.startswith('dma_module:=') for value in guest))

    def test_direct_bundle_requires_the_notification_abi(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / 'guest.tar.gz'
            for abi in (None, 0, 1):
                marker = dict(version=4, transport_protocol=3, transport_backends=['m5', 'm5ops'])
                if abi is not None:
                    marker['m5_abi'] = abi
                with tarfile.open(bundle, 'w:gz') as archive:
                    data = json.dumps(marker).encode()
                    entry = tarfile.TarInfo('opt/chimaera/latency/latency-runtime.json')
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
                if abi == 1:
                    validate_bundle(bundle, backend='m5ops')
                else:
                    with self.assertRaisesRegex(ValueError, 'direct m5ops ABI 1'):
                        validate_bundle(bundle, backend='m5ops')
