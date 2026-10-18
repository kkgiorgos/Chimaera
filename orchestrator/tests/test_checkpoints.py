import io
import json
from pathlib import Path
import subprocess
import tarfile
from unittest.mock import patch

import pytest

from checkpoints import package, sparse_copy, unpack
from common import digest


def test_import_checks_disk_identity_and_does_not_mark_corrupt_assets_ready(tmp_path):
    image = 'sha256:' + 'a' * 64
    directory = tmp_path / 'readiness-assets'
    def copy(command, destination):
        assert command[:2] == ['docker', 'cp'] and command[-1] == '-'
        (directory / 'checkpoints').mkdir()
        for name in ('guest.img', 'kernel', 'guest.tar.gz'):
            (directory / name).write_text(name)
        index = dict(provenance=dict(disk_sha256='different disk',
            kernel_sha256=digest(directory / 'kernel'),
            bundle_sha256=digest(directory / 'guest.tar.gz')))
        (directory / 'checkpoints/index.json').write_text(json.dumps(index))
    # Build the expected index independently; image bytes must match this identity.
    fixture = tmp_path / 'expected.json'
    kernel = tmp_path / 'kernel'
    bundle = tmp_path / 'bundle'
    kernel.write_text('kernel')
    bundle.write_text('guest.tar.gz')
    fixture.write_text(json.dumps(dict(provenance=dict(disk_sha256='different disk',
        kernel_sha256=digest(kernel), bundle_sha256=digest(bundle)))))
    with patch('checkpoints.subprocess.run') as commands, patch(
            'checkpoints.sparse_copy', side_effect=copy):
        with pytest.raises(ValueError, match='asset differs: guest.img'):
            unpack(tmp_path, image, digest(fixture))
    assert not (directory / 'import.json').exists()
    assert any(call.args[0][:2] == ['docker', 'rm'] for call in commands.call_args_list)


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


def test_package_sparse_disk_in_one_readable_layer_without_changing_source(tmp_path):
    prepared = tmp_path / 'inputs/prepared'
    identifier = 'b' * 64
    checkpoint = prepared / 'checkpoints' / identifier
    checkpoint.mkdir(parents=True)
    (checkpoint / 'checkpoint.json').write_text('saved')
    index = prepared / 'checkpoints/index.json'
    index.write_text(json.dumps(dict(version=1, groups={identifier: {'checkpoint.json': 'hash'}})))
    (prepared / 'guest.tar.gz').write_bytes(b'guest bundle')
    disk, kernel = tmp_path / 'disk', tmp_path / 'kernel'
    with disk.open('wb') as stream:
        stream.write(b'backing disk')
        stream.seek(64 * 1024 * 1024)
        stream.write(b'end')
    disk.chmod(0o600)
    kernel.write_bytes(b'kernel')
    original = digest(disk)
    execute = subprocess.run

    def command(arguments, **kwargs):
        if arguments[:3] == ['docker', 'buildx', 'build']:
            context = Path(arguments[-1])
            dockerfile = (context / 'Dockerfile').read_text()
            assert 'ADD readiness.tar.gz /opt/chimaera/readiness/' in dockerfile
            assert 'RUN chmod' not in dockerfile
            assert not (context / 'readiness').exists()
            archive = context / 'readiness.tar.gz'
            assert archive.stat().st_size < 100_000
            with tarfile.open(archive) as stream:
                member = stream.getmember('./guest.img')
                # Docker rejects the older GNU sparse ('S') header type.
                assert member.type == tarfile.REGTYPE
                assert member.sparse is not None
                assert member.size == disk.stat().st_size
                assert member.mode & 0o444 == 0o444
                assert member.uid == member.gid == 0
                assert stream.extractfile(member).read() == disk.read_bytes()
                assert stream.extractfile('./checkpoints/' + identifier + '/checkpoint.json').read() == b'saved'
            return None
        if arguments[0] == 'docker':
            return None
        return execute(arguments, **kwargs)

    with patch('checkpoints.subprocess.run', side_effect=command), patch(
            'checkpoints.run', return_value='sha256:' + 'c' * 64 + '\n'):
        image, expected = package(tmp_path, 'sha256:' + 'a' * 64, disk, kernel)
    assert image == 'sha256:' + 'c' * 64
    assert expected == digest(index)
    assert digest(disk) == original
    assert disk.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / 'checkpoint-image').exists()
