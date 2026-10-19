import io
import json
from pathlib import Path
import subprocess
import tarfile
from unittest.mock import patch

import pytest

from checkpoints import package, sparse_copy, unpack
from common import digest


def test_import_checks_disk_identity_and_rejects_corrupt_assets(tmp_path):
    directory = tmp_path / 'shared-assets'
    (directory / 'checkpoints').mkdir(parents=True)
    for name in ('guest.img', 'kernel', 'guest.tar.gz'):
        (directory / name).write_text(name)
    index = dict(groups={}, provenance=dict(disk_sha256='different disk',
        kernel_sha256=digest(directory / 'kernel'), bundle_sha256=digest(directory / 'guest.tar.gz')))
    (directory / 'checkpoints/index.json').write_text(json.dumps(index))
    with pytest.raises(ValueError, match='asset differs: guest.img'):
        unpack(tmp_path, 'unused image', digest(directory / 'checkpoints/index.json'))


def test_sparse_import_restores_dense_zero_regions_without_expanding_them(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.mkdir()
    destination.mkdir()
    disk = source / 'guest.img'
    disk.write_bytes(b'disk header' + b'\0' * (8 * 1024 * 1024) + b'disk end')
    assert disk.stat().st_blocks * 512 >= 8 * 1024 * 1024
    sparse_copy(['tar', '-cf', '-', '-C', str(source), '.'],
                destination)
    imported = destination / 'guest.img'
    assert digest(imported) == digest(disk)
    assert imported.stat().st_size == disk.stat().st_size
    assert imported.stat().st_blocks * 512 < 1024 * 1024


def test_sparse_import_reports_export_failures(tmp_path):
    with pytest.raises(RuntimeError, match='checkpoint export failed: .*producer failed'):
        sparse_copy(['sh', '-c', 'echo "producer failed" >&2; exit 7'], tmp_path)


@pytest.mark.parametrize('kind', ['parent', 'absolute', 'symlink'])
def test_sparse_import_rejects_paths_and_links_outside_destination(tmp_path, kind):
    destination = tmp_path / 'destination'
    destination.mkdir()
    outside = tmp_path / 'escape'
    archive = tmp_path / 'unsafe.tar'
    member = tarfile.TarInfo('../escape' if kind == 'parent' else
                             str(outside) if kind == 'absolute' else 'link')
    with tarfile.open(archive, 'w') as stream:
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = str(outside)
            stream.addfile(member)
        else:
            member.size = 1
            stream.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Invalid readiness archive'):
        sparse_copy(['cat', str(archive)], destination)
    assert not outside.exists()


def test_package_and_transfer_sparse_disk_without_a_copy_or_docker_layer(tmp_path):
    prepared = tmp_path / 'inputs/prepared'
    identifier = 'b' * 64
    checkpoint = prepared / 'checkpoints' / identifier
    checkpoint.mkdir(parents=True)
    state = checkpoint / 'checkpoint.json'
    state.write_text('saved')
    bundle = prepared / 'guest.tar.gz'
    bundle.write_bytes(b'guest bundle')
    disk, kernel = tmp_path / 'disk', tmp_path / 'kernel'
    with disk.open('wb') as stream:
        stream.write(b'backing disk')
        stream.seek(64 * 1024 * 1024)
        stream.write(b'end')
    disk.chmod(0o600)
    kernel.write_bytes(b'kernel')
    original = digest(disk)
    index = prepared / 'checkpoints/index.json'
    index.write_text(json.dumps(dict(version=1, groups={identifier: {'checkpoint.json': digest(state)}},
        provenance=dict(disk_sha256=original, kernel_sha256=digest(kernel), bundle_sha256=digest(bundle)))))
    application = 'sha256:' + 'a' * 64
    with patch('checkpoints.subprocess.run') as docker:
        image, expected = package(tmp_path, application, disk, kernel)
        docker.assert_not_called()
    assert image == application
    assert expected == digest(index)
    export = tmp_path / 'export-assets'
    assert (export / 'guest.img').is_symlink()
    assert (export / 'guest.img').resolve() == disk
    # Exercise the real GNU tar transport format and extraction, including holes.
    archive = tmp_path / 'wire.tar.gz'
    subprocess.run(['tar', '--dereference', '--sparse', '--format=pax', '--sparse-version=1.0',
        '--mode=a+rX', '-czf', str(archive), '-C', str(export), '.'], check=True)
    assert archive.stat().st_size < 100_000
    destination = tmp_path / 'shared-assets'
    destination.mkdir()
    subprocess.run(['tar', '-xzSf', str(archive), '-C', str(destination)], check=True)
    assert (destination / 'guest.img').stat().st_blocks * 512 < 1024 * 1024
    assert unpack(tmp_path, application, expected) == destination
    assert digest(disk) == original
    assert disk.stat().st_mode & 0o777 == 0o600
    state_import = destination / 'checkpoints' / identifier / 'checkpoint.json'
    state_import.write_text('corrupted memory')
    with pytest.raises(ValueError, match='checkpoint file differs'):
        unpack(tmp_path, application, expected)
