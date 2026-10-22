#!/usr/bin/env python3
"""Freeze explicit source/runtime selections without launching any process."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import tarfile

from common import (COMMON_FILES, DIAGNOSTIC_VARIANTS, IMPLEMENTATION_FILES, IMPLEMENTATION_MODES,
                    PERFORMANCE_VARIANTS, VARIANTS, read_json, sha256,
                    tree_hashes, write_json)
from legacy import selected_implementation


def file_record(path):
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha256(path))


def bundle_common(path, requested=COMMON_FILES):
    hashes = {}
    with tarfile.open(path, 'r:*') as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = member.name.lstrip('./')
            for relative in requested:
                if name.endswith('/app/' + relative):
                    if relative in hashes:
                        raise ValueError('Ambiguous guest bundle member: ' + relative)
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as handle:
                        for data in iter(lambda: handle.read(4 * 1024 * 1024), b''):
                            digest.update(data)
                    hashes[relative] = digest.hexdigest()
    return hashes


def freeze(selection, root):
    root = root.resolve()
    result = dict(schema_version=1, created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  physical_host=selection['physical_host'],
                  environment=selection['environment'], assets={}, variants={})
    result['main_performance_implementations'] = list(PERFORMANCE_VARIANTS)
    result['diagnostic_implementations'] = list(DIAGNOSTIC_VARIANTS)
    def resolve(value):
        path = Path(value)
        return path if path.is_absolute() else root / path

    for key in ('image', 'kernel', 'gem5', 'gem5_config', 'benchmark_source'):
        result['assets'][key] = file_record(resolve(selection[key]))
    result['gem5_root'] = str(resolve(selection['gem5_root']).resolve())
    orchestration = resolve(selection['orchestration']).resolve()
    result['orchestration'] = dict(path=str(orchestration), files=tree_hashes(orchestration))
    result['tooling_files'] = tree_hashes(Path(__file__).resolve().parent)
    for variant in VARIANTS:
        chosen = selection.get('variants', {}).get(variant, {})
        entry = dict(selection=chosen, ready=False, problems=[],
                     comparison_role='diagnostic' if variant in DIAGNOSTIC_VARIANTS else 'main')
        result['variants'][variant] = entry
        try:
            if (chosen.get('backend'), chosen.get('middleware')) != IMPLEMENTATION_MODES[variant]:
                raise ValueError('Backend/middleware differs from implementation label')
            for key in ('source', 'bundle'):
                entry[key] = file_record(resolve(chosen[key]))
            legacy_implementation = selected_implementation(variant, chosen)
            if legacy_implementation:
                entry['retained_bundle'] = file_record(resolve(chosen['retained_bundle']))
                entry['legacy_implementation'] = legacy_implementation
                from legacy import IDENTITIES, READY_SOURCE_KIND
                if IDENTITIES[legacy_implementation]['source_kind'] == READY_SOURCE_KIND:
                    entry['source_snapshot'] = file_record(resolve(chosen['source_snapshot']))
            runtime = resolve(chosen['runtime']).resolve()
            if not runtime.is_dir():
                raise ValueError('Missing runtime directory: ' + str(runtime))
            entry['runtime'] = str(runtime)
            if legacy_implementation:
                entry['stage_provenance'] = file_record(runtime.parent / 'stage-provenance.json')
            entry['runtime_files'] = tree_hashes(runtime)
            entry['common_files'] = {name: entry['runtime_files'][name] for name in COMMON_FILES}
            entry['guest_common_files'] = bundle_common(entry['bundle']['path'])
            if entry['guest_common_files'] != entry['common_files']:
                raise ValueError('Host/guest endpoint, clocks or RMW hashes differ')
            entry['implementation_files'] = {name: entry['runtime_files'][name] for name in IMPLEMENTATION_FILES}
            entry['guest_implementation_files'] = bundle_common(entry['bundle']['path'], IMPLEMENTATION_FILES)
            for name, digest in entry['guest_implementation_files'].items():
                if entry['implementation_files'][name] != digest:
                    raise ValueError('Host/guest implementation identity differs: ' + name)
            if IMPLEMENTATION_FILES[1] not in entry['guest_implementation_files']:
                raise ValueError('Guest bridge implementation bytes missing from bundle')
            with tarfile.open(entry['bundle']['path']) as archive:
                from legacy import member_bytes
                actual_guest = member_bytes(archive, 'opt/chimaera/latency/runtime/guest_bridge')
                entry['actual_guest_bridge_sha256'] = hashlib.sha256(actual_guest).hexdigest()
                entry['guest_start_sha256'] = hashlib.sha256(member_bytes(
                    archive, 'opt/chimaera/latency/guest_start')).hexdigest()
                entry['guest_metadata'] = json.loads(member_bytes(
                    archive, 'opt/chimaera/latency/latency-runtime.json'))
                entry['guest_loader_common_files'] = {name: hashlib.sha256(member_bytes(
                    archive, 'opt/chimaera/latency/' + name)).hexdigest()
                    for name in COMMON_FILES[1:]}
            if entry['guest_loader_common_files'] != {name: entry['common_files'][name]
                                                     for name in COMMON_FILES[1:]}:
                raise ValueError('Guest loader endpoint dependencies differ from common application assets')
            if entry['actual_guest_bridge_sha256'] != entry['implementation_files'][IMPLEMENTATION_FILES[1]]:
                raise ValueError('Actual guest startup bridge differs from selected implementation')
            expected_endpoint = selection['benchmark_endpoint_sha256']
            if entry['common_files'][COMMON_FILES[0]] != expected_endpoint:
                raise ValueError('Runtime does not contain the selected extended benchmark endpoint')
            entry['backend'] = chosen['backend']
            entry['middleware'] = chosen['middleware']
            if not legacy_implementation and (entry['guest_metadata'].get('version') != 4 or
                    entry['guest_metadata'].get('transport_protocol') != 3):
                raise ValueError('Legacy guest metadata needs a separately reviewed, hash-bound '
                                 'adapter for this implementation; do not relabel protocol3')
            if chosen['backend'] == 'm5ops' and entry['guest_metadata'].get('m5_abi') != 1:
                raise ValueError('Direct m5ops bundle metadata lacks m5_abi=1; parent stage_guest must retain it')
            if chosen['backend'] in ('dma', 'm5ops'):
                entry['module'] = file_record(resolve(chosen['module']))
            if legacy_implementation:
                from legacy import validate
                entry['legacy_identity_validation'] = validate(entry['bundle']['path'], entry,
                    implementation=legacy_implementation, sdk_image=result['environment']['sdk_image'])
            entry['ready'] = True
        except (OSError, KeyError, ValueError, tarfile.TarError) as error:
            entry['problems'].append(str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to replace an immutable selection lock')
    result = freeze(read_json(args.selection), args.root)
    result['selection_sha256'] = sha256(args.selection)
    write_json(args.output, result)
    print('Frozen selection; not-ready variants:', ', '.join(
        name for name, value in result['variants'].items() if not value['ready']))


if __name__ == '__main__':
    main()
