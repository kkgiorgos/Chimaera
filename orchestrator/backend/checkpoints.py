"""Package readiness plus its exact backing disk in a transferable application image."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from common import atomic_json, digest, run


LOCATION = '/opt/chimaera/readiness'


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cp', '--reflink=auto', '--sparse=always', '--preserve=timestamps',
                    '--', str(source), str(destination)], check=True)


def package(directory, application, disk, kernel):
    """The original disk remains untouched; only a read-only copy enters the image."""
    prepared = directory / 'inputs/prepared'
    index = json.loads((prepared / 'checkpoints/index.json').read_text())
    if index.get('version') != 1 or not index.get('groups'):
        raise ValueError('Readiness preparation did not produce any checkpoints')
    context = directory / 'checkpoint-image'
    assets = context / 'readiness'
    assets.mkdir(parents=True)
    for name, source in (('guest.img', disk), ('kernel', kernel),
                         ('guest.tar.gz', prepared / 'guest.tar.gz')):
        copy_file(source, assets / name)
    copy_file(prepared / 'checkpoints/index.json', assets / 'checkpoints/index.json')
    for identifier, files in index['groups'].items():
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise ValueError('Invalid readiness checkpoint identifier')
        for name in files:
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid readiness checkpoint file')
            copy_file(prepared / 'checkpoints' / identifier / path,
                      assets / 'checkpoints' / identifier / path)
    base = 'chimaera:ball-checkpoint-base-' + application.removeprefix('sha256:')
    subprocess.run(['docker', 'image', 'tag', application, base], check=True)
    (context / 'Dockerfile').write_text(f'FROM {base}\nUSER root\n'
        f'COPY readiness/ {LOCATION}/\nRUN chmod -R a+rX {LOCATION}\n')
    tag = 'chimaera:ball-ready-' + directory.name
    subprocess.run(['docker', 'buildx', 'build', '--load', '--provenance=false',
        '--platform', 'linux/amd64', '--progress', 'plain', '-t', tag, str(context)], check=True)
    image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
    # The image is the durable copy. Keep the producer's preparation logs and
    # checkpoint files, but discard this additional construction copy.
    shutil.rmtree(context)
    return image, digest(prepared / 'checkpoints/index.json')


def unpack(directory, image, expected):
    """Consumers import the producer disk; independently constructed disks cannot restore it."""
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Invalid readiness application image ID')
    destination = directory / 'readiness-assets'
    marker = destination / 'import.json'
    if marker.is_file():
        record = json.loads(marker.read_text())
        if record != dict(image=image, index_sha256=expected):
            raise ValueError('Imported readiness image identity changed')
        if digest(destination / 'checkpoints/index.json') != expected:
            raise ValueError('Imported readiness index changed')
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    name = 'chimaera-readiness-import-' + uuid.uuid4().hex[:12]
    command = ['docker', 'create', '--name', name]
    operation = os.environ.get('CHIMAERA_OPERATION')
    if operation:
        command += ['--label', 'chimaera.experiment=' + operation]
    subprocess.run([*command, image], check=True)
    try:
        subprocess.run(['docker', 'cp', name + ':' + LOCATION + '/.', str(destination)], check=True)
    finally:
        subprocess.run(['docker', 'rm', name], check=True)
    if digest(destination / 'checkpoints/index.json') != expected:
        raise ValueError('Readiness application image contains a different checkpoint index')
    index = json.loads((destination / 'checkpoints/index.json').read_text())
    for name, field in (('guest.img', 'disk_sha256'), ('kernel', 'kernel_sha256'),
                        ('guest.tar.gz', 'bundle_sha256')):
        if digest(destination / name) != index['provenance'][field]:
            raise ValueError('Readiness image asset differs: ' + name)
    atomic_json(marker, dict(image=image, index_sha256=expected))
    return destination
