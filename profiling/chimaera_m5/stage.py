#!/usr/bin/env python3
"""Copy a retained runtime/bundle and replace common benchmark assets, without running them.

This does not change bridge protocols or claim that an old runtime is qualified.
Use isolated output paths; never stage into an original retained runtime.
"""
import argparse
import copy
import io
from pathlib import Path
import shutil
import tarfile

from common import COMMON_FILES, sha256, tree_hashes, write_json
from legacy import LEGACY_IMPLEMENTATIONS


def stage(runtime, bundle, common_runtime, endpoint, output, legacy_original=False, bootstrap=None,
          legacy_implementation=None, legacy_source=None, sdk_image=None):
    if legacy_original:
        if legacy_implementation not in (None, 'original_m5'):
            raise ValueError('--legacy-original is restricted to original_m5')
        legacy_implementation = 'original_m5'
    retained_runtime_files = tree_hashes(runtime) if legacy_implementation else None
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'runtime'
    shutil.copytree(runtime, target, symlinks=False)
    replacements = {}
    for relative in COMMON_FILES:
        source = endpoint if relative == COMMON_FILES[0] else common_runtime / relative
        if not source.is_file():
            raise ValueError('Missing common benchmark artifact: ' + str(source))
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        replacements[relative] = destination
    extra, legacy_record = {}, None
    if legacy_implementation:
        if bootstrap is None:
            raise ValueError('Legacy stage requires --bootstrap with current Python package')
        from legacy import prepare
        extra, legacy_record, dependencies = prepare(target, bundle, bootstrap, common_runtime,
            implementation=legacy_implementation, source_provenance=legacy_source, sdk_image=sdk_image)
        for name, source in dependencies.items():
            shutil.copy2(source, target / 'lib' / name)
    # The common RMW may need dependencies absent from an older bridge prefix.
    # Add only missing libraries; existing transport implementations stay selected.
    dependencies = {}
    for source in sorted((common_runtime / 'lib').glob('*.so*')):
        if source.is_file() and not (target / 'lib' / source.name).exists():
            dependencies[source.name] = source
            shutil.copy2(source, target / 'lib' / source.name)
    selected_prefix = None
    with tarfile.open(bundle, 'r:*') as archive:
        for member in archive:
            suffix = '/app/' + COMMON_FILES[0]
            if member.name.endswith(suffix):
                prefix = member.name[:-len(suffix)] + '/app/'
                if selected_prefix is not None:
                    raise ValueError('Ambiguous endpoint root in guest bundle')
                selected_prefix = prefix
    if selected_prefix is None:
        raise ValueError('Guest endpoint root not found')
    guest_root = selected_prefix[:-len('app/')]
    # Guest startup normally searches this loader directory before app/lib.
    # Match those copies too, while preserving every implementation library.
    for relative, path in replacements.items():
        if relative != COMMON_FILES[0]:
            extra[guest_root + relative] = (path.read_bytes(), path.stat().st_mode & 0o777)
    for name, path in dependencies.items():
        data = (path.read_bytes(), path.stat().st_mode & 0o777)
        extra[selected_prefix + 'lib/' + name] = data
        extra[guest_root + 'lib/' + name] = data
    target_bundle = output / 'guest.tar.gz'
    with tarfile.open(bundle, 'r:*') as source, tarfile.open(target_bundle, 'x:gz') as destination:
        for member in source:
            normalized = member.name.lstrip('./')
            relative = (normalized[len(selected_prefix.lstrip('./')):]
                        if normalized.startswith(selected_prefix.lstrip('./')) else None)
            if relative in replacements or normalized in extra:
                continue
            handle = source.extractfile(member) if member.isfile() else None
            destination.addfile(copy.copy(member), handle)
            if handle is not None:
                handle.close()
        for relative, path in replacements.items():
            member = tarfile.TarInfo(selected_prefix + relative)
            member.size = path.stat().st_size
            member.mode = path.stat().st_mode & 0o777
            with path.open('rb') as handle:
                destination.addfile(member, handle)
        for name, (data, mode) in extra.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), mode
            destination.addfile(member, io.BytesIO(data))
    record = dict(parent_runtime=str(runtime.resolve()), parent_bundle=str(bundle.resolve()),
        parent_bundle_sha256=sha256(bundle), bundle_sha256=sha256(target_bundle),
        common_files={relative: sha256(path) for relative, path in replacements.items()},
        added_dependencies={name: sha256(path) for name, path in dependencies.items()},
        guest_loader_common_files={relative: sha256(path) for relative, path in replacements.items()
                                   if relative != COMMON_FILES[0]},
        legacy_adapter=legacy_record, retained_runtime_files=retained_runtime_files,
        qualification='PENDING: protocol, capability metadata and backend compatibility must be '
            'established after the parent catch gate. No runtime validation was performed; '
            'old lossless/bridge semantics must not be relabeled by replacing an endpoint.')
    write_json(output / 'stage-provenance.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime', 'bundle', 'common-runtime', 'endpoint', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    legacy_flags = parser.add_mutually_exclusive_group()
    legacy_flags.add_argument('--legacy-original', action='store_true', help='Alias for original_m5 only')
    legacy_flags.add_argument('--legacy-implementation', choices=LEGACY_IMPLEMENTATIONS)
    parser.add_argument('--bootstrap', type=Path)
    parser.add_argument('--legacy-source', type=Path, help='Retained source archive/provenance; defaults to pinned catalog path')
    parser.add_argument('--sdk-image', help='Immutable common application SDK image; defaults to pinned catalog identity')
    args = parser.parse_args()
    stage(args.runtime, args.bundle, args.common_runtime, args.endpoint, args.output,
          args.legacy_original, args.bootstrap, args.legacy_implementation, args.legacy_source, args.sdk_image)


if __name__ == '__main__':
    main()
