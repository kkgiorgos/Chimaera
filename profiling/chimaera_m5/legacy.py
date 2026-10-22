"""Separate hash-bound legacy pairs; no legacy metadata is relabeled protocol3."""
import hashlib
import json
import posixpath
from pathlib import Path
import tarfile

from common import COMMON_FILES, read_json, sha256, write_json

CATALOG_PATH = Path(__file__).with_name('legacy-identities.json')
CATALOG = read_json(CATALOG_PATH)
IDENTITIES = CATALOG['implementations']
LEGACY_IMPLEMENTATIONS = tuple(IDENTITIES)
GUEST_ROOT = 'opt/chimaera/latency/'
RECORD = GUEST_ROOT + 'm5-benchmark-legacy.json'
GUEST_CATALOG = GUEST_ROOT + 'runtime/chimaera_ros/_benchmark_legacy_identities.json'
SUPPORTED_ENDPOINT_FEATURES = {'lossless', 'image_integrity'}
DRIVER = 'lib/chimaera_ros_bridge/latency_driver'
GUEST_BRIDGE = 'lib/chimaera_ros_bridge/guest_bridge'
READY_SOURCE_KIND = 'retained_ready_source_snapshot_binary_correspondence_unverified'


def selected_implementation(implementation, selection):
    """Require an explicit, label-matched opt-in; old original flag stays scoped."""
    selected = selection.get('legacy_implementation')
    if type(selection.get('legacy_original_adapter', False)) is not bool:
        raise ValueError('legacy_original_adapter must be boolean')
    if selection.get('legacy_original_adapter'):
        if implementation != 'original_m5' or selected not in (None, 'original_m5'):
            raise ValueError('legacy_original_adapter is restricted to original_m5')
        selected = 'original_m5'
    if implementation in IDENTITIES:
        if selected != implementation:
            raise ValueError('Legacy selection requires its own explicit legacy_implementation')
        return selected
    if selected is not None:
        raise ValueError('Legacy adapter cannot select a modern implementation')
    return None


def adapt_routes(routes):
    """All three reviewed legacy strict schemas reject inactive group metadata."""
    for route in routes['topics']:
        if 'transport_class' in route:
            raise ValueError('Legacy bridge does not implement explicit DDS lane route metadata')
        if not route.get('coalesce', False):
            route.pop('coalesce_group', None)
    return routes


def member_bytes(archive, name):
    matches = [member for member in archive.getmembers()
               if posixpath.normpath(member.name.lstrip('/')) == name]
    if len(matches) != 1 or matches[0].name != name or not matches[0].isfile():
        raise ValueError('Legacy identity requires one regular archive member: ' + name)
    with archive.extractfile(matches[0]) as handle:
        return handle.read()


def check_digest(data, expected, label):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(label + ' identity differs')


def check_source_bundle(archive, identity):
    for name, key in (
            ('app/' + DRIVER, 'host_driver_sha256'),
            ('app/' + GUEST_BRIDGE, 'app_guest_bridge_sha256'),
            ('runtime/guest_bridge', 'executed_guest_bridge_sha256'),
            ('guest_start', 'guest_start_sha256'),
            ('latency-runtime.json', 'metadata_sha256')):
        check_digest(member_bytes(archive, GUEST_ROOT + name), identity[key], name)


def check_source_provenance(path, identity):
    """Pin retained evidence without upgrading a snapshot to exact build source."""
    path = Path(path)
    if sha256(path) != identity['source_sha256']:
        raise ValueError('Retained source archive/provenance identity differs')
    claim = dict(path=str(path.resolve()), kind=identity['source_kind'], sha256=identity['source_sha256'])
    if identity['source_kind'] == READY_SOURCE_KIND:
        provenance = read_json(path)
        if (provenance.get('kind') != READY_SOURCE_KIND or
                provenance.get('implementation') != 'initialized_m5_ready' or
                provenance.get('exact_binary_source_correspondence') != 'UNVERIFIED' or
                provenance.get('source_confidence') != identity['source_confidence'] or
                provenance.get('source_snapshot', {}).get('sha256') != identity['source_snapshot_sha256']):
            raise ValueError('READY provenance must retain its unverified source correspondence')
        snapshot = path.parent / provenance['source_snapshot']['path']
        if sha256(snapshot) != identity['source_snapshot_sha256']:
            raise ValueError('READY retained source snapshot identity differs')
        claim.update(confidence=identity['source_confidence'],
                     exact_binary_source_correspondence='UNVERIFIED',
                     snapshot_sha256=identity['source_snapshot_sha256'])
    return claim


def check_catalog(archive, proof, implementation, identity):
    """Admit a frozen prior catalog only for its unchanged, pre-existing pins."""
    pinned = proof.get('catalog_sha256')
    current = sha256(CATALOG_PATH)
    if pinned != current and implementation not in CATALOG.get('compatible_prior_catalogs', {}).get(pinned, ()):
        raise ValueError('Guest identity catalog is not pinned for this implementation')
    data = member_bytes(archive, GUEST_CATALOG)
    check_digest(data, pinned, 'Guest identity catalog')
    catalog = json.loads(data)
    if (catalog.get('application_sdk_image') != CATALOG['application_sdk_image'] or
            catalog.get('implementations', {}).get(implementation) != identity):
        raise ValueError('Guest identity catalog changes selected implementation pins')


def prepare(runtime, source_bundle, bootstrap, common_runtime, implementation='original_m5',
            source_provenance=None, sdk_image=None):
    """Prepare new bytes and provenance only; preserve every legacy executable."""
    if implementation not in IDENTITIES:
        raise ValueError('Unknown legacy implementation')
    identity = IDENTITIES[implementation]
    for relative, key in ((DRIVER, 'host_driver_sha256'), (GUEST_BRIDGE, 'app_guest_bridge_sha256')):
        if sha256(runtime / relative) != identity[key]:
            raise ValueError(implementation + ' retained driver/bridge identity differs: ' + relative)
    if sha256(source_bundle) != identity['retained_bundle_sha256']:
        raise ValueError(implementation + ' retained source bundle identity differs')
    source_provenance = (Path(source_provenance) if source_provenance is not None else
                         Path(__file__).resolve().parents[2] / identity['source'])
    source_claim = check_source_provenance(source_provenance, identity)
    sdk_image = sdk_image or CATALOG['application_sdk_image']
    if sdk_image != CATALOG['application_sdk_image']:
        raise ValueError('Common application SDK identity differs from pinned legacy cohort')
    with tarfile.open(source_bundle) as archive:
        check_source_bundle(archive, identity)
    replacements = {}
    for name in ('__init__.py', 'manifest.py', 'dds.py'):
        replacements[GUEST_ROOT + 'runtime/chimaera_ros/' + name] = ((bootstrap / name).read_bytes(), 0o644)
    replacements[GUEST_ROOT + 'runtime/chimaera_ros/_benchmark_current_runner.py'] = (
        (bootstrap / 'runner.py').read_bytes(), 0o644)
    replacements[GUEST_ROOT + 'runtime/chimaera_ros/runner.py'] = (
        Path(__file__).with_name('legacy_guest_runner.py').read_bytes(), 0o644)
    replacements[GUEST_CATALOG] = (CATALOG_PATH.read_bytes(), 0o644)
    # Preserve guest_start, both bridge locations and legacy metadata exactly.
    dependencies = {}
    for source in sorted((common_runtime / 'lib').glob('*.so*')):
        if source.is_file() and not (runtime / 'lib' / source.name).exists():
            dependencies[source.name] = source
    for name, source in dependencies.items():
        replacements[GUEST_ROOT + 'app/lib/' + name] = (source.read_bytes(), source.stat().st_mode & 0o777)
    record = dict(schema_version=2, implementation=implementation,
        transport_identity=identity['transport_identity'], transport_protocol=None,
        source_bundle_sha256=identity['retained_bundle_sha256'],
        source_provenance=source_claim,
        application_sdk_image=sdk_image, historical_sdk_image=identity['historical_sdk_image'],
        driver_sha256=identity['host_driver_sha256'],
        app_guest_bridge_sha256=identity['app_guest_bridge_sha256'],
        executed_guest_bridge_sha256=identity['executed_guest_bridge_sha256'],
        original_metadata_sha256=identity['metadata_sha256'],
        guest_start_sha256=identity['guest_start_sha256'], catalog_sha256=sha256(CATALOG_PATH),
        extended_endpoint_sha256=sha256(runtime / COMMON_FILES[0]),
        bootstrap_files={name: hashlib.sha256(data).hexdigest() for name, (data, mode) in replacements.items()},
        qualification='PREPARED ONLY: parent must run this preserved pair under KVM after '
                      'the actual-catching gate; identity checks do not establish a runtime pass or protocol3.')
    replacements[RECORD] = (json.dumps(record, indent=2).encode() + b'\n', 0o644)
    write_json(runtime / 'share/chimaera_ros_bridge/config/m5-benchmark-legacy.json', record)
    capability = runtime / 'share/chimaera_ros_bridge/config/latency_capabilities.json'
    metadata = read_json(capability)
    if metadata.get('transport_protocol') is not None:
        raise ValueError('Legacy capability metadata unexpectedly declares a protocol')
    # Endpoint features describe new application bytes, not legacy delivery results.
    metadata['endpoint_features'] = sorted(SUPPORTED_ENDPOINT_FEATURES)
    metadata['benchmark_endpoint_sha256'] = record['extended_endpoint_sha256']
    write_json(capability, metadata)
    return replacements, record, dependencies


def validate(bundle, entry, required_features=(), implementation='original_m5', sdk_image=None):
    if implementation not in IDENTITIES:
        raise ValueError('Unknown legacy implementation')
    identity = IDENTITIES[implementation]
    if entry.get('backend') != 'm5' or entry.get('middleware') != 'bridge':
        raise ValueError('Legacy qualification cannot select another backend')
    if set(required_features) - SUPPORTED_ENDPOINT_FEATURES:
        raise ValueError('Legacy qualification does not support requested middleware features')
    if Path(bundle).resolve() != Path(entry['bundle']['path']).resolve() or sha256(bundle) != entry['bundle']['sha256']:
        raise ValueError('Legacy qualification bundle differs from frozen selection')
    if entry['runtime_files'].get(DRIVER) != identity['host_driver_sha256'] or (
            entry['runtime_files'].get(GUEST_BRIDGE) != identity['app_guest_bridge_sha256']):
        raise ValueError(implementation + ' frozen driver/bridge identity differs')
    if entry.get('source', {}).get('sha256') != identity['source_sha256']:
        raise ValueError('Frozen source archive/provenance differs from legacy selection')
    if entry.get('selection', {}).get('source_kind', identity['source_kind']) != identity['source_kind']:
        raise ValueError('Frozen source provenance kind differs from legacy selection')
    if identity['source_kind'] == READY_SOURCE_KIND:
        claim = check_source_provenance(entry['source']['path'], identity)
        snapshot = entry.get('source_snapshot', {})
        if (snapshot.get('sha256') != identity['source_snapshot_sha256'] or
                sha256(snapshot['path']) != identity['source_snapshot_sha256']):
            raise ValueError('Frozen READY retained source snapshot differs')
    if entry.get('retained_bundle', {}).get('sha256') != identity['retained_bundle_sha256']:
        raise ValueError('Frozen retained bundle differs from legacy selection')
    sdk_image = sdk_image or CATALOG['application_sdk_image']
    if sdk_image != CATALOG['application_sdk_image']:
        raise ValueError('Frozen common application SDK identity differs')
    with tarfile.open(bundle) as archive:
        proof = json.loads(member_bytes(archive, RECORD))
        expected = dict(schema_version=2, implementation=implementation,
            transport_identity=identity['transport_identity'], transport_protocol=None,
            source_bundle_sha256=identity['retained_bundle_sha256'],
            application_sdk_image=sdk_image, historical_sdk_image=identity['historical_sdk_image'],
            driver_sha256=identity['host_driver_sha256'],
            app_guest_bridge_sha256=identity['app_guest_bridge_sha256'],
            executed_guest_bridge_sha256=identity['executed_guest_bridge_sha256'],
            original_metadata_sha256=identity['metadata_sha256'], guest_start_sha256=identity['guest_start_sha256'])
        if any(key not in proof or proof[key] != value for key, value in expected.items()):
            raise ValueError('Legacy proof misidentifies selected pair, protocol or source/SDK identity')
        if proof.get('source_provenance', {}).get('sha256') != identity['source_sha256'] or (
                proof.get('source_provenance', {}).get('kind') != identity['source_kind']):
            raise ValueError('Legacy proof source archive/provenance differs')
        if identity['source_kind'] == READY_SOURCE_KIND and any(
                proof.get('source_provenance', {}).get(key) != value for key, value in claim.items() if key != 'path'):
            raise ValueError('Legacy proof overstates READY source correspondence')
        check_source_bundle(archive, identity)
        check_catalog(archive, proof, implementation, identity)
        endpoint_sha = hashlib.sha256(member_bytes(archive, GUEST_ROOT + 'app/' + COMMON_FILES[0])).hexdigest()
        if endpoint_sha != entry['common_files'][COMMON_FILES[0]] or endpoint_sha != proof['extended_endpoint_sha256']:
            raise ValueError('Extended endpoint identity differs')
        required_bootstrap = {GUEST_CATALOG, *(GUEST_ROOT + 'runtime/chimaera_ros/' + name for name in
            ('__init__.py', 'manifest.py', 'dds.py', '_benchmark_current_runner.py', 'runner.py'))}
        if not required_bootstrap <= set(proof.get('bootstrap_files', {})):
            raise ValueError('Legacy proof omits required declarative bootstrap files')
        check_digest(member_bytes(archive, GUEST_ROOT + 'runtime/chimaera_ros/runner.py'),
                     sha256(Path(__file__).with_name('legacy_guest_runner.py')), 'Guest runner shim')
        for name, digest in proof['bootstrap_files'].items():
            check_digest(member_bytes(archive, name), digest, 'Declarative bootstrap ' + name)
    return dict(implementation=implementation, transport_identity=identity['transport_identity'],
        transport_protocol=None, original_metadata_unchanged=True,
        actual_guest_bridge_sha256=identity['executed_guest_bridge_sha256'],
        host_driver_sha256=identity['host_driver_sha256'], source_sha256=identity['source_sha256'],
        retained_bundle_sha256=identity['retained_bundle_sha256'], application_sdk_image=sdk_image,
        endpoint_sha256=endpoint_sha,
        source_kind=identity['source_kind'], source_confidence=identity.get('source_confidence'),
        runtime_qualification='not established by identity validation')
