"""Declarative bootstrap for one explicitly selected, pinned legacy binary pair."""
import hashlib
import json
from pathlib import Path
import sys

from . import _benchmark_current_runner as current

ACTIVE_IMPLEMENTATION = None
ALLOWED_PARAMETERS = {
    'config_file', 'max_serialized_bytes', 'max_pending_messages', 'max_image_age_ns',
    'image_diagnostics', 'm5ops', 'wait_for_application', 'startup_timeout_s',
    'simulation_time_from_readfile', 'checkpoint_control', 'application_ready_file',
}
original_plan = current.plan


def plan(data, side, package_prefix=None, simulate=False):
    bridge = data['bridge']
    if ACTIVE_IMPLEMENTATION is None or side != 'guest' or simulate or bridge['transport_backend'] != 'm5' or (
            bridge['middleware_transport'] != 'bridge'):
        raise ValueError('Pinned legacy bootstrap accepts only guest-side m5/bridge')
    spec = original_plan(data, side, package_prefix, simulate)
    command = spec['processes'][0]['command']
    stripped, index = [], 0
    while index < len(command):
        if command[index] == '-p':
            value = command[index + 1]
            key = value.split(':=', 1)[0]
            if key in ALLOWED_PARAMETERS:
                stripped.extend(command[index:index + 2])
            index += 2
        else:
            stripped.append(command[index])
            index += 1
    spec['processes'][0]['command'] = stripped
    for key in list(spec['env']):
        if key.startswith('CHIMAERA_DDS_') or key == 'CHIMAERA_M5_DEVICE':
            spec['env'].pop(key)
    # Apply after sourcing overlays, as current.supervise does. These are the
    # common application ABI libraries, not a substituted bridge implementation.
    prefix = '/opt/chimaera/latency/app/lib:/opt/chimaera/latency/lib:'
    spec['env']['LD_LIBRARY_PATH'] = prefix + spec['env'].get('LD_LIBRARY_PATH', '')
    return spec


def main():
    global ACTIVE_IMPLEMENTATION
    root = Path('/opt/chimaera/latency')
    proof = json.loads((root / 'm5-benchmark-legacy.json').read_text())
    catalog = json.loads(Path(__file__).with_name('_benchmark_legacy_identities.json').read_text())
    implementation = proof.get('implementation')
    if implementation not in catalog['implementations']:
        raise ValueError('Unknown legacy implementation; refusing bootstrap')
    identity = catalog['implementations'][implementation]
    if proof.get('schema_version') != 2 or proof.get('transport_identity') != identity['transport_identity'] or (
            proof.get('transport_protocol') is not None or
            proof.get('source_bundle_sha256') != identity['retained_bundle_sha256'] or
            proof.get('application_sdk_image') != catalog['application_sdk_image']):
        raise ValueError('Legacy proof disagrees with selected pair/source/SDK')
    for relative, key in (('runtime/guest_bridge', 'executed_guest_bridge_sha256'),
                          ('app/lib/chimaera_ros_bridge/guest_bridge', 'app_guest_bridge_sha256'),
                          ('app/lib/chimaera_ros_bridge/latency_driver', 'host_driver_sha256'),
                          ('latency-runtime.json', 'metadata_sha256'), ('guest_start', 'guest_start_sha256')):
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != identity[key]:
            raise ValueError(implementation + ' preserved artifact identity differs: ' + relative)
    binary = root / 'runtime/guest_bridge'
    if '--guest-binary' not in sys.argv or sys.argv[sys.argv.index('--guest-binary') + 1] != str(binary):
        raise ValueError('Legacy bootstrap requires its pinned guest bridge path')
    ACTIVE_IMPLEMENTATION = implementation
    current.plan = plan
    return current.main()


if __name__ == '__main__':
    sys.exit(main())
