"""Share readiness assets without copying a guest disk into Docker layers."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from common import digest


LOCATION = '/opt/chimaera/readiness'


def sparse_copy(command, destination):
    """Extract a tar stream while preserving zero regions as file holes."""
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            archive_error = None
            zeros = bytes(64 * 1024)
            try:
                with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                    for member in archive:
                        path = Path(member.name)
                        target = destination / path
                        if (path.is_absolute() or '..' in path.parts or
                                not target.resolve().is_relative_to(destination.resolve())):
                            raise ValueError('Invalid readiness archive path')
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                        elif member.isfile():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with archive.extractfile(member) as source, target.open('wb') as output:
                                for block in iter(lambda: source.read(len(zeros)), b''):
                                    if block == zeros[:len(block)]:
                                        output.seek(len(block), os.SEEK_CUR)
                                    else:
                                        output.write(block)
                                output.truncate(member.size)
                            target.chmod(member.mode & 0o777)
                            os.utime(target, (member.mtime, member.mtime))
                        else:
                            raise ValueError('Invalid readiness archive member')
                # Drain padding after the tar end marker before awaiting Docker.
                while process.stdout.read(len(zeros)):
                    pass
            except tarfile.TarError as error:
                archive_error = error
            process.stdout.close()
            process.wait(timeout=10)
            if process.returncode or archive_error:
                errors.seek(0)
                detail = errors.read().decode(errors='replace') or str(archive_error)
                raise RuntimeError('Readiness checkpoint export failed: ' + detail[-2000:])
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()


def package(directory, application, disk, kernel):
    """Expose the exact producer assets for sparse streaming, without copying bytes."""
    prepared = directory / 'inputs/prepared'
    index_path = prepared / 'checkpoints/index.json'
    index = json.loads(index_path.read_text())
    if index.get('version') != 1 or not index.get('groups'):
        raise ValueError('Readiness preparation did not produce any checkpoints')
    export = directory / 'export-assets'
    if export.exists():
        shutil.rmtree(export)
    export.mkdir()

    def expose(name, source):
        target = export / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(Path(source).resolve(strict=True))

    expose('guest.img', disk)
    expose('kernel', kernel)
    expose('guest.tar.gz', prepared / 'guest.tar.gz')
    expose('checkpoints/index.json', index_path)
    for identifier, files in index['groups'].items():
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise ValueError('Invalid readiness checkpoint identifier')
        for name in files:
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid readiness checkpoint file')
            expose(Path('checkpoints') / identifier / path,
                   prepared / 'checkpoints' / identifier / path)
    return application, digest(index_path)


def unpack(directory, image, expected, destination=None):
    """Validate directly streamed assets; no disk ever enters the Docker image store."""
    destination = destination or directory / 'shared-assets'
    if digest(destination / 'checkpoints/index.json') != expected:
        raise ValueError('Transferred readiness assets contain a different checkpoint index')
    index = json.loads((destination / 'checkpoints/index.json').read_text())
    for name, field in (('guest.img', 'disk_sha256'), ('kernel', 'kernel_sha256'),
                        ('guest.tar.gz', 'bundle_sha256')):
        if digest(destination / name) != index['provenance'][field]:
            raise ValueError('Readiness asset differs: ' + name)
    for identifier, files in index['groups'].items():
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise ValueError('Invalid readiness checkpoint identifier')
        for name, expected_hash in files.items():
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid readiness checkpoint file')
            if digest(destination / 'checkpoints' / identifier / path) != expected_hash:
                raise ValueError('Readiness checkpoint file differs: ' + name)
    return destination
