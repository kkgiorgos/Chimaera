"""Prepared legacy identity regressions; do not execute before the catch gate."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common import COMMON_FILES, IMPLEMENTATION_FILES, sha256, tree_hashes
from freeze import file_record, freeze
import legacy
from stage import stage


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(path, members):
    with tarfile.open(path, 'w:gz') as output:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))


@contextmanager
def fixtures():
    """Distinct fake pair bytes and pinned catalog; no ELF is loaded or run."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        common, bootstrap = root / 'common', root / 'bootstrap'
        bootstrap.mkdir()
        for name in ('__init__.py', 'runner.py', 'manifest.py', 'dds.py'):
            (bootstrap / name).write_text('# declarative fixture\n')
        for relative in COMMON_FILES:
            path = common / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('common:' + relative).encode())
        cases, identities = {}, {}
        for implementation in legacy.LEGACY_IMPLEMENTATIONS:
            old = root / implementation
            for relative in (*COMMON_FILES, *IMPLEMENTATION_FILES):
                path = old / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((implementation + ':' + relative).encode())
            capability = old / 'share/chimaera_ros_bridge/config/latency_capabilities.json'
            capability.parent.mkdir(parents=True)
            capability.write_text('{"version":4,"workloads":["ball_catching"]}')
            source = root / (implementation + '-source.tar.gz')
            source.write_bytes(('retained source:' + implementation).encode())
            source_snapshot = None
            if implementation == 'initialized_m5_ready':
                source_snapshot = source
                source = root / (implementation + '-provenance.json')
                source.write_text(json.dumps(dict(kind=legacy.READY_SOURCE_KIND,
                    implementation=implementation, exact_binary_source_correspondence='UNVERIFIED',
                    source_confidence='fixture_unverified',
                    source_snapshot=dict(path=source_snapshot.name, sha256=sha256(source_snapshot)))))
            metadata = b'{"version":4,"cpu_types":["kvm","timing"],"workloads":["ball_catching"]}'
            startup = b'guest_start uses pinned runtime/guest_bridge\n'
            guest = (old / legacy.GUEST_BRIDGE).read_bytes()
            members = {legacy.GUEST_ROOT + 'app/' + name: (old / name).read_bytes()
                       for name in (*COMMON_FILES, *IMPLEMENTATION_FILES)}
            members.update({legacy.GUEST_ROOT + 'latency-runtime.json': metadata,
                            legacy.GUEST_ROOT + 'runtime/guest_bridge': guest,
                            legacy.GUEST_ROOT + 'guest_start': startup})
            bundle = root / (implementation + '.tar.gz')
            archive(bundle, members)
            identities[implementation] = dict(transport_identity='fixture:' + implementation,
                source=str(source), source_kind='fixture_source_archive', source_sha256=sha256(source),
                retained_bundle_sha256=sha256(bundle), historical_sdk_image=None,
                host_driver_sha256=sha256(old / legacy.DRIVER), app_guest_bridge_sha256=digest(guest),
                executed_guest_bridge_sha256=digest(guest), metadata_sha256=digest(metadata),
                guest_start_sha256=digest(startup))
            cases[implementation] = dict(runtime=old, source=source, bundle=bundle, members=members)
            if source_snapshot:
                identities[implementation].update(source_kind=legacy.READY_SOURCE_KIND,
                    source_confidence='fixture_unverified', source_snapshot_sha256=sha256(source_snapshot))
                cases[implementation]['source_snapshot'] = source_snapshot
        catalog = dict(schema_version=2, application_sdk_image='sha256:fixture-sdk', implementations=identities)
        catalog_path = root / 'legacy-identities.json'
        catalog_path.write_text(json.dumps(catalog))
        with patch.multiple(legacy, IDENTITIES=identities, CATALOG=catalog, CATALOG_PATH=catalog_path):
            yield SimpleNamespace(root=root, common=common, bootstrap=bootstrap, cases=cases, catalog=catalog)


def staged(fixture, implementation, original_alias=False):
    case = fixture.cases[implementation]
    output = fixture.root / ('matched-' + implementation)
    stage(case['runtime'], case['bundle'], fixture.common, fixture.common / COMMON_FILES[0], output,
          legacy_original=original_alias, bootstrap=fixture.bootstrap,
          legacy_implementation=None if original_alias else implementation,
          legacy_source=case['source'], sdk_image=fixture.catalog['application_sdk_image'])
    selected_bundle = output / 'guest.tar.gz'
    entry = dict(backend='m5', middleware='bridge',
        bundle=file_record(selected_bundle), source=file_record(case['source']),
        retained_bundle=file_record(case['bundle']), runtime_files=tree_hashes(output / 'runtime'),
        common_files={name: sha256(output / 'runtime' / name) for name in COMMON_FILES})
    if 'source_snapshot' in case:
        entry['source_snapshot'] = file_record(case['source_snapshot'])
    return output, entry


class LegacyIdentityTests(unittest.TestCase):
    def test_old_schema_removes_only_inactive_group_metadata(self):
        adjusted = legacy.adapt_routes(dict(topics=[
            dict(coalesce=False, coalesce_group='stereo'), dict(coalesce=True, coalesce_group='stereo')]))
        self.assertNotIn('coalesce_group', adjusted['topics'][0])
        self.assertEqual(adjusted['topics'][1]['coalesce_group'], 'stereo')
        with self.assertRaisesRegex(ValueError, 'lane route metadata'):
            legacy.adapt_routes(dict(topics=[dict(transport_class='bulk')]))

    def test_original_alias_cannot_admit_either_rewrite(self):
        self.assertEqual(legacy.selected_implementation('original_m5', dict(legacy_original_adapter=True)), 'original_m5')
        for name in ('streamed_m5', 'initialized_m5', 'initialized_m5_ready'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'restricted to original_m5'):
                legacy.selected_implementation(name, dict(legacy_original_adapter=True, legacy_implementation=name))
        with self.assertRaises(ValueError):
            legacy.selected_implementation('streamed_m5', dict(legacy_implementation='initialized_m5'))
        with self.assertRaises(ValueError):
            legacy.selected_implementation('direct_dds_m5ops', dict(legacy_implementation='original_m5'))
        with self.assertRaises(ValueError):
            legacy.selected_implementation('original_m5', dict(legacy_original_adapter='false'))

    def test_no_legacy_pair_can_select_dds_or_direct_backend(self):
        for name in legacy.LEGACY_IMPLEMENTATIONS:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'another backend'):
                legacy.validate(Path('unused'), dict(backend='m5ops', middleware='fastdds'), implementation=name)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'middleware features'):
                legacy.validate(Path('unused'), dict(backend='m5', middleware='bridge'),
                                ['fastdds_transport'], implementation=name)

    def test_every_pair_preserves_all_binary_and_startup_bytes(self):
        with fixtures() as fixture:
            for name in fixture.cases:
                with self.subTest(name=name):
                    output, entry = staged(fixture, name, original_alias=name == 'original_m5')
                    proof = legacy.validate(entry['bundle']['path'], entry, ['lossless', 'image_integrity'],
                        implementation=name, sdk_image=fixture.catalog['application_sdk_image'])
                    self.assertEqual(proof['implementation'], name)
                    self.assertIsNone(proof['transport_protocol'])
                    self.assertTrue(proof['original_metadata_unchanged'])
                    for relative in IMPLEMENTATION_FILES:
                        self.assertEqual((output / 'runtime' / relative).read_bytes(),
                                         (fixture.cases[name]['runtime'] / relative).read_bytes())
                    with tarfile.open(entry['bundle']['path']) as bundle:
                        for member in ('app/' + legacy.DRIVER, 'app/' + legacy.GUEST_BRIDGE,
                                       'runtime/guest_bridge', 'guest_start', 'latency-runtime.json'):
                            path = legacy.GUEST_ROOT + member
                            self.assertEqual(legacy.member_bytes(bundle, path), fixture.cases[name]['members'][path])

    def test_cross_pair_reuse_and_default_original_scope_rejected(self):
        with fixtures() as fixture:
            _, streamed = staged(fixture, 'streamed_m5')
            with self.assertRaisesRegex(ValueError, 'driver/bridge identity'):
                legacy.validate(streamed['bundle']['path'], streamed)  # Defaults to original only.
            with self.assertRaisesRegex(ValueError, 'driver/bridge identity'):
                legacy.validate(streamed['bundle']['path'], streamed, implementation='initialized_m5')

    def test_re_freezing_tampered_critical_members_does_not_weaken_pins(self):
        with fixtures() as fixture:
            for name in fixture.cases:
                _, entry = staged(fixture, name)
                with tarfile.open(entry['bundle']['path']) as bundle:
                    members = {m.name: legacy.member_bytes(bundle, m.name) for m in bundle if m.isfile()}
                for member in ('app/' + legacy.DRIVER, 'app/' + legacy.GUEST_BRIDGE,
                               'runtime/guest_bridge', 'guest_start', 'latency-runtime.json'):
                    with self.subTest(implementation=name, member=member):
                        modified = fixture.root / 'modified.tar.gz'
                        archive(modified, dict(members, **{legacy.GUEST_ROOT + member: b'changed'}))
                        tampered = dict(entry, bundle=file_record(modified))
                        with self.assertRaisesRegex(ValueError, 'identity differs'):
                            legacy.validate(modified, tampered, implementation=name)

    def test_source_archive_retained_bundle_sdk_and_bootstrap_are_bound(self):
        with fixtures() as fixture:
            _, entry = staged(fixture, 'initialized_m5')
            for field in ('source', 'retained_bundle'):
                changed = dict(entry, **{field: dict(entry[field], sha256='changed')})
                with self.subTest(field=field), self.assertRaises(ValueError):
                    legacy.validate(entry['bundle']['path'], changed, implementation='initialized_m5')
            with self.assertRaisesRegex(ValueError, 'SDK identity'):
                legacy.validate(entry['bundle']['path'], entry, implementation='initialized_m5', sdk_image='wrong-sdk')
            with tarfile.open(entry['bundle']['path']) as bundle:
                members = {m.name: legacy.member_bytes(bundle, m.name) for m in bundle if m.isfile()}
            members[legacy.GUEST_ROOT + 'runtime/chimaera_ros/runner.py'] = b'changed shim'
            modified = fixture.root / 'changed-shim.tar.gz'
            archive(modified, members)
            with self.assertRaisesRegex(ValueError, 'runner shim'):
                legacy.validate(modified, dict(entry, bundle=file_record(modified)), implementation='initialized_m5')

    def test_ready_guest_cannot_be_used_as_final_initialized_pair(self):
        with fixtures() as fixture:
            case = fixture.cases['initialized_m5']
            (case['runtime'] / legacy.GUEST_BRIDGE).write_bytes(b'guest-v2-ready instead of final')
            with self.assertRaisesRegex(ValueError, 'retained driver/bridge identity'):
                legacy.prepare(case['runtime'], case['bundle'], fixture.bootstrap, fixture.common,
                               implementation='initialized_m5', source_provenance=case['source'])

    def test_observed_ready_hash_is_rejected_by_real_final_identity(self):
        identity = legacy.IDENTITIES['initialized_m5']
        ready_hash = identity['rejected_mixed_prefix']['observed_guest_bridge_sha256']
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / 'unused-after-binary-check'
            bundle.write_bytes(b'identity-only fixture; not executed')
            entry = dict(backend='m5', middleware='bridge', bundle=file_record(bundle),
                runtime_files={legacy.DRIVER: identity['host_driver_sha256'], legacy.GUEST_BRIDGE: ready_hash})
            with self.assertRaisesRegex(ValueError, 'frozen driver/bridge identity'):
                legacy.validate(bundle, entry, implementation='initialized_m5')

    def test_ready_and_final_real_pins_remain_distinct(self):
        ready, final = (legacy.IDENTITIES[name] for name in ('initialized_m5_ready', 'initialized_m5'))
        self.assertEqual(ready['app_guest_bridge_sha256'], final['rejected_mixed_prefix']['observed_guest_bridge_sha256'])
        self.assertEqual(ready['retained_bundle_sha256'], final['rejected_mixed_prefix']['matching_ready_bundle_sha256'])
        self.assertEqual(final['app_guest_bridge_sha256'], 'cb0671f5628b6b97507e074656cdfe3ef5569ba32ca3ea0b3148be25096804ae')
        self.assertEqual(final['source_sha256'], 'cd2d87c6e6022b948e8cc0103ad0d1788c2da28ef4f5c593d1780eab2063d05e')
        self.assertEqual(ready['source_kind'], legacy.READY_SOURCE_KIND)
        for labels in legacy.CATALOG['compatible_prior_catalogs'].values():
            self.assertNotIn('initialized_m5_ready', labels)

    def test_ready_snapshot_drift_and_overstated_source_proof_rejected(self):
        with fixtures() as fixture:
            _, entry = staged(fixture, 'initialized_m5_ready')
            with tarfile.open(entry['bundle']['path']) as bundle:
                members = {m.name: legacy.member_bytes(bundle, m.name) for m in bundle if m.isfile()}
            proof = json.loads(members[legacy.RECORD])
            proof['source_provenance']['exact_binary_source_correspondence'] = 'VERIFIED'
            members[legacy.RECORD] = json.dumps(proof).encode()
            modified = fixture.root / 'overstated.tar.gz'
            archive(modified, members)
            with self.assertRaisesRegex(ValueError, 'overstates'):
                legacy.validate(modified, dict(entry, bundle=file_record(modified)), implementation='initialized_m5_ready')
            Path(entry['source_snapshot']['path']).write_bytes(b'drift')
            with self.assertRaisesRegex(ValueError, 'snapshot identity'):
                legacy.validate(entry['bundle']['path'], entry, implementation='initialized_m5_ready')

    def test_prior_catalog_requires_exact_digest_and_unchanged_selected_entry(self):
        with fixtures() as fixture:
            _, entry = staged(fixture, 'streamed_m5')
            prior_hash = sha256(legacy.CATALOG_PATH)
            fixture.catalog['compatible_prior_catalogs'] = {prior_hash: ['streamed_m5']}
            legacy.CATALOG_PATH.write_text(json.dumps(fixture.catalog))
            legacy.validate(entry['bundle']['path'], entry, implementation='streamed_m5')
            fixture.catalog['implementations']['streamed_m5']['transport_identity'] = 'changed'
            with tarfile.open(entry['bundle']['path']) as bundle, self.assertRaisesRegex(ValueError, 'changes selected'):
                legacy.check_catalog(bundle, dict(catalog_sha256=prior_hash), 'streamed_m5', legacy.IDENTITIES['streamed_m5'])
            with tarfile.open(entry['bundle']['path']) as bundle, self.assertRaisesRegex(ValueError, 'not pinned'):
                legacy.check_catalog(bundle, dict(catalog_sha256=prior_hash), 'initialized_m5_ready',
                                     legacy.IDENTITIES['initialized_m5_ready'])

    def test_alternate_archive_name_cannot_shadow_checked_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'duplicate.tar.gz'
            name = legacy.GUEST_ROOT + 'runtime/guest_bridge'
            archive(path, {name: b'expected', './' + name: b'overwrites on extraction'})
            with tarfile.open(path) as bundle, self.assertRaisesRegex(ValueError, 'one regular'):
                legacy.member_bytes(bundle, name)

    def test_freeze_accepts_each_explicit_legacy_pair_without_protocol_relabeling(self):
        with fixtures() as fixture:
            variants = {}
            for name in fixture.cases:
                output, entry = staged(fixture, name)
                variants[name] = dict(runtime=str(output / 'runtime'), bundle=entry['bundle']['path'],
                    source=entry['source']['path'], retained_bundle=entry['retained_bundle']['path'],
                    backend='m5', middleware='bridge', legacy_implementation=name)
                if 'source_snapshot' in entry:
                    variants[name]['source_snapshot'] = entry['source_snapshot']['path']
            artifact = fixture.root / 'artifact'
            artifact.write_bytes(b'not executed')
            selection = dict(physical_host='fixture', environment=dict(sdk_image=fixture.catalog['application_sdk_image']),
                variants=variants, gem5_root=str(fixture.root), orchestration=str(fixture.bootstrap),
                benchmark_endpoint_sha256=sha256(fixture.common / COMMON_FILES[0]),
                **{key: str(artifact) for key in ('image', 'kernel', 'gem5', 'gem5_config', 'benchmark_source')})
            frozen = freeze(selection, fixture.root)
            for name in fixture.cases:
                with self.subTest(name=name):
                    self.assertTrue(frozen['variants'][name]['ready'], frozen['variants'][name]['problems'])
                    self.assertNotIn('transport_protocol', frozen['variants'][name]['guest_metadata'])
                    self.assertEqual(frozen['variants'][name]['legacy_identity_validation']['implementation'], name)
            # An app/lib-only check must not admit stale loader copies that win
            # the guest's LD_LIBRARY_PATH search before the application prefix.
            name = 'streamed_m5'
            selected_bundle = Path(variants[name]['bundle'])
            with tarfile.open(selected_bundle) as bundle:
                members = {m.name: legacy.member_bytes(bundle, m.name) for m in bundle if m.isfile()}
            members[legacy.GUEST_ROOT + COMMON_FILES[-1]] = b'stale loader RMW'
            archive(selected_bundle, members)
            rejected = freeze(selection, fixture.root)['variants'][name]
            self.assertFalse(rejected['ready'])
            self.assertIn('Guest loader endpoint dependencies differ', rejected['problems'][0])

    def test_adapter_dispatch_stays_scoped_to_selected_pair(self):
        from adapter import install
        production = Mock(return_value='production validator')
        module = SimpleNamespace(parameters=lambda *args: None, routes=lambda *args: dict(topics=[]),
                                 traffic=lambda config: [], dds_profile=lambda config: None,
                                 validate_bundle=production, guest_session=lambda *args: None)
        active = install(module)
        with patch('legacy.validate', return_value='identity proof') as validate:
            for name in legacy.LEGACY_IMPLEMENTATIONS:
                active.update(implementation=name, selection=dict(selection=dict(legacy_implementation=name)),
                              sdk_image='fixture-sdk')
                self.assertEqual(module.validate_bundle('bundle', 'm5'), 'identity proof')
                self.assertEqual(validate.call_args.kwargs['implementation'], name)
            active['implementation'] = 'direct_dds_m5ops'
            self.assertEqual(module.validate_bundle('bundle', 'm5ops'), 'production validator')
        production.assert_called_once()


if __name__ == '__main__':
    unittest.main()
