import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from chimaera_ros.latency import routes, validate
from chimaera_ros.latency_config import DEFAULT, plan_sweep
from chimaera_ros.latency_session import validate_bundle
from chimaera_ros.latency_workloads import image_age
from chimaera_ros.dds import shared_memory_profile
from chimaera_ros.latency import guest_session
import xml.etree.ElementTree as ET


class DmaConfigurationTest(unittest.TestCase):
    def test_large_image_dds_profile_is_loopback_and_sizes_the_actual_shm_transport(self):
        size = 16 * 1024 * 1024
        root = ET.fromstring(shared_memory_profile(size))
        ns = {'d': 'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
        transports = root.findall('d:transport_descriptors/d:transport_descriptor', ns)
        self.assertEqual(len(transports), 2)
        self.assertEqual(transports[0].find('d:interfaceWhiteList/d:address', ns).text, '127.0.0.1')
        self.assertEqual(int(transports[1].find('d:segment_size', ns).text), size)
        self.assertEqual(root.find('d:participant/d:rtps/d:useBuiltinTransports', ns).text, 'false')
        guest = guest_session(config=dict(dds_shm_bytes=size))['guest']
        self.assertFalse(guest['localhost_only'])
        self.assertEqual(guest['env']['RMW_IMPLEMENTATION'], 'rmw_fastrtps_cpp')
        self.assertEqual(guest['env']['FASTRTPS_DEFAULT_PROFILES_FILE'], '/tmp/chimaera-latency-dds.xml')
        plan = plan_sweep(dict(DEFAULT, workload='ball_catching', dds_shm_bytes=size))
        self.assertTrue(all(c['config']['dds_shm_bytes'] == size for c in plan['cases']))
        for small in (512 * 1024, 1024 * 1024):
            with self.assertRaises(ValueError):
                plan_sweep(dict(DEFAULT, workload='ball_catching', dds_shm_bytes=small))

    def test_dma_requires_fresh_boot_and_preserves_case_parameters(self):
        with self.assertRaisesRegex(ValueError, 'fresh boots'):
            plan_sweep(dict(DEFAULT, transport_backend='dma'))
        plan = plan_sweep(dict(DEFAULT, transport_backend='dma', single_boot=False,
                               dma_module='/test/driver.ko', delivery_policy='lossless'))
        for case in plan['cases']:
            self.assertEqual(case['config']['transport_backend'], 'dma')
            self.assertEqual(case['config']['delivery_policy'], 'lossless')
            self.assertEqual(case['config']['dma_module'], '/test/driver.ko')
        for changed in [dict(delivery_policy='unknown'), dict(verify_payload='yes')]:
            with self.assertRaises(ValueError):
                plan_sweep(dict(DEFAULT, **changed))

    def test_lossless_stereo_keeps_atomic_pairs_without_replacement_or_age_expiry(self):
        config = dict(workload='ball_catching', delivery_policy='lossless', physics_step_ns=1000000)
        topics = routes(config=config)['topics']
        images = [r for r in topics if r['type'] == 'sensor_msgs/msg/Image']
        self.assertEqual(len(images), 2)
        self.assertTrue(all(r['coalesce_group'] == 'stereo' and not r['coalesce'] for r in images))
        self.assertTrue(all(r['qos']['depth'] == 128 and r['qos']['reliability'] == 'reliable' for r in images))
        self.assertEqual(image_age(config), 0)

    def test_old_m5_bundle_cannot_start_a_dma_case(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guest.tar.gz'
            for dma in [None, 0, 1]:
                marker = dict(version=4, transport_protocol=3)
                if dma is not None:
                    marker.update(transport_backends=['m5', 'dma'], dma_abi=dma, dma_protocol=2)
                with tarfile.open(path, 'w:gz') as archive:
                    data = json.dumps(marker).encode()
                    member = tarfile.TarInfo('opt/chimaera/latency/latency-runtime.json')
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                validate_bundle(path)
                with self.assertRaisesRegex(ValueError, 'endpoint features'):
                    validate_bundle(path, required_features=('lossless',))
                if dma == 1:
                    validate_bundle(path, backend='dma')
                else:
                    with self.assertRaisesRegex(ValueError, 'DMA ABI 1'):
                        validate_bundle(path, backend='dma')


if __name__ == '__main__':
    unittest.main()
