import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import types
import unittest
from unittest.mock import patch

from chimaera_ros.dds import stage_libraries
from chimaera_ros.latency_session import validate_bundle


class RuntimeLibrariesTest(unittest.TestCase):
    def test_old_fastdds_bundle_without_libraries_is_rejected_before_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / 'guest.tar.gz'
            for libraries in (False, True):
                with tarfile.open(bundle, 'w:gz') as archive:
                    files = {'latency-runtime.json': json.dumps(dict(version=4,
                        transport_protocol=3, endpoint_features=['fastdds_transport'])).encode()}
                    if libraries:
                        files.update({'lib/' + name: b'library' for name in (
                            'librmw_fastrtps_cpp.so', 'librmw_fastrtps_shared_cpp.so',
                            'libchimaera_dds_transport.so')})
                    for name, data in files.items():
                        entry = tarfile.TarInfo('opt/chimaera/latency/' + name)
                        entry.size = len(data)
                        archive.addfile(entry, io.BytesIO(data))
                if libraries:
                    validate_bundle(bundle, required_features=('fastdds_transport',))
                else:
                    with self.assertRaisesRegex(ValueError, 'rebuild and stage'):
                        validate_bundle(bundle, required_features=('fastdds_transport',))

    def test_freezes_rmw_dependencies_and_rejects_missing_or_conflicting_libraries(self):
        with tempfile.TemporaryDirectory(prefix='chimaera library ') as temporary:
            root = Path(temporary)
            plugin = root / 'sdk/lib/librmw_fastrtps_cpp.so'
            plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b'selected RMW')
            dependency = root / 'libchimaera_dds_transport.so'
            dependency.write_bytes(b'transport')
            output = f'libchimaera_dds_transport.so => {dependency} (0x1234)\n'
            output += 'libc.so.6 => /lib/libc.so.6 (0x5678)\n'
            packages = types.ModuleType('ament_index_python.packages')
            packages.get_package_prefix = lambda _: str(root / 'sdk')
            modules = {'ament_index_python': types.ModuleType('ament_index_python'),
                       'ament_index_python.packages': packages}
            with patch.dict(sys.modules, modules), patch('subprocess.run', return_value=
                    subprocess.CompletedProcess([], 0, stdout=output)) as ldd:
                copied = stage_libraries(root / 'guest/lib', [root / 'guest_bridge'])
                self.assertEqual((root / 'guest/lib'/plugin.name).read_bytes(), plugin.read_bytes())
                self.assertEqual((root / 'guest/lib'/dependency.name).read_bytes(), dependency.read_bytes())
                self.assertNotIn('libc.so.6', copied)
                self.assertEqual(len(ldd.call_args_list), 2)
                json.dumps(copied)  # Manifest values must remain serializable.
                (root / 'guest/lib'/dependency.name).write_bytes(b'conflicting ABI')
                with self.assertRaisesRegex(ValueError, 'Conflicting'):
                    stage_libraries(root / 'guest/lib', [])
                ldd.return_value.stdout = 'libchimaera_dds_transport.so => not found\n'
                with self.assertRaisesRegex(ValueError, 'Missing runtime library'):
                    stage_libraries(root / 'guest2/lib', [])


if __name__ == '__main__':
    unittest.main()
