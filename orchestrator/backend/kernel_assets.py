"""Build, cache and share inseparable guest kernel/transport-driver pairs."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

from common import atomic_json, content_identity, digest, run


BUILDER = 'chimaera:orchestrator-kernel-builder'
FILES = ('kernel', 'chimaera_dma.ko', 'kernel.config', 'Module.symvers', 'build.json')
MODULES = dict(dma='chimaera_dma.ko', m5ops='chimaera_m5.ko')


def asset_files(backend):
    if backend not in MODULES:
        raise ValueError('Unsupported kernel/module backend: ' + str(backend))
    return ('kernel', MODULES[backend], 'kernel.config', 'Module.symvers', 'build.json')


def verify(directory, expected=None, backend=None):
    directory = Path(directory)
    manifest = directory / 'kernel-assets.json'
    if expected and digest(manifest) != expected:
        raise ValueError('Transferred kernel/module manifest differs from the producer')
    record = json.loads(manifest.read_text())
    # Version 1 DMA pairs predate explicit backend metadata and remain usable.
    actual_backend = record.get('backend', 'dma')
    if backend is not None and actual_backend != backend:
        raise ValueError('Kernel/module backend differs from the requested transport')
    files = asset_files(actual_backend)
    if record.get('version') != 1 or set(record.get('sha256', {})) != set(files):
        raise ValueError('Invalid kernel/module asset manifest')
    for name, expected_hash in record['sha256'].items():
        if not (directory / name).is_file() or digest(directory / name) != expected_hash:
            raise ValueError('Kernel/module asset differs: ' + name)
    build = json.loads((directory / 'build.json').read_text())
    if not build.get('kernel_release') or build.get('vermagic', '').split()[0:1] != [build['kernel_release']]:
        raise ValueError('Kernel and transport module release mismatch')
    if actual_backend == 'm5ops':
        if (record.get('module') != MODULES[actual_backend] or record.get('abi_version') != 1 or
                build.get('backend') != actual_backend or build.get('module') != MODULES[actual_backend] or
                build.get('abi_version') != 1):
            raise ValueError('Invalid m5ops module/ABI metadata; expected ABI 1')
    return directory / 'kernel', directory / MODULES[actual_backend]


def download(policy, destination):
    """An interrupted or corrupt download is never a usable cached source."""
    if destination.is_file() and digest(destination) == policy['sha256']:
        return
    partial = destination.with_suffix('.' + uuid.uuid4().hex + '.partial')
    try:
        with urllib.request.urlopen(policy['url'], timeout=120) as source, partial.open('wb') as output:
            shutil.copyfileobj(source, output)
        if digest(partial) != policy['sha256']:
            raise ValueError('Linux source checksum mismatch')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def ensure(root, directory, jobs, backend='dma'):
    files = asset_files(backend)
    root, directory = Path(root), Path(directory)
    assets = root / '.chimaera-orchestrator/assets'
    assets.mkdir(parents=True, exist_ok=True)
    # Preparation normally runs once. Also protect manual/recovery invocations.
    with (assets / 'kernel-build.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recipe = root / 'orchestrator/kernel'
        recipe_key = content_identity(root, ['orchestrator/kernel/Dockerfile'])
        builder_marker = assets / 'kernel-builder.json'
        cached = json.loads(builder_marker.read_text()) if builder_marker.exists() else {}
        present = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', BUILDER],
                                 capture_output=True, text=True)
        if present.returncode or cached.get('recipe') != recipe_key or cached.get('image') != present.stdout.strip():
            atomic_json(directory / 'stage.json', dict(phase='Building guest kernel toolchain'))
            subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '-t', BUILDER,
                            '-f', str(recipe / 'Dockerfile'), str(recipe)], check=True)
        builder = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', BUILDER], text=True).strip()
        atomic_json(builder_marker, dict(recipe=recipe_key, image=builder))
        atomic_json(assets / ('image-' + builder.removeprefix('sha256:') + '.json'), dict(image_id=builder))
        sources = ['orchestrator/kernel/Dockerfile', 'orchestrator/kernel/build.py',
                   'orchestrator/kernel/policy.json', 'orchestrator/kernel/config.fragment',
                   'gem5-transport/kernel/Makefile',
                   'gem5-transport/kernel/' + MODULES[backend].replace('.ko', '.c'),
                   'gem5/include/gem5/' + MODULES[backend].replace('.ko', '.h'),
                   'orchestrator/backend/kernel_assets.py']
        inputs = content_identity(root, sources)
        key = hashlib.sha256((backend + builder + inputs).encode()).hexdigest()
        destination = assets / ('kernel-' + key)
        if destination.exists():
            pair = verify(destination, backend=backend)
        else:
            atomic_json(directory / 'stage.json', dict(phase='Downloading verified Linux source'))
            policy = json.loads((recipe / 'policy.json').read_text())
            archive = assets / ('linux-' + policy['version'] + '.tar.xz')
            download(policy, archive)
            atomic_json(directory / 'stage.json', dict(phase=f'Building matching guest kernel and {backend} module'))
            with tempfile.TemporaryDirectory(prefix='kernel-incoming-', dir=assets) as temporary:
                incoming = Path(temporary)
                command = ['docker', 'run', '--rm', '--init', '--user', f'{os.getuid()}:{os.getgid()}']
                operation = os.environ.get('CHIMAERA_OPERATION')
                if operation:
                    command += ['--label', 'chimaera.experiment=' + operation]
                for source, target in [(recipe, '/recipe'), (archive.parent, '/source'),
                                       (root / 'gem5-transport/kernel', '/driver'), (root / 'gem5', '/gem5')]:
                    # The archive has a versioned host name and a fixed helper path.
                    if target == '/source':
                        source, target = archive, '/source/linux.tar.xz'
                    command += ['--mount', f'type=bind,source={source},target={target},readonly']
                command += ['--mount', f'type=bind,source={incoming},target=/output', builder, str(int(jobs)), backend]
                log = directory / 'kernel-build.log'
                with log.open('w') as output:
                    try:
                        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
                    except subprocess.CalledProcessError as error:
                        raise RuntimeError('Guest kernel/module build failed; see kernel-build.log') from error
                if content_identity(root, sources) != inputs:
                    raise ValueError('Kernel/module build sources changed during compilation; retry preparation')
                atomic_json(incoming / 'kernel-assets.json', dict(version=1, key=key, builder=builder,
                    backend=backend, module=MODULES[backend], abi_version=1,
                    inputs=inputs, sha256={name: digest(incoming / name) for name in files}))
                verify(incoming, backend=backend)
                incoming.rename(destination)
            pair = verify(destination, backend=backend)
        atomic_json(assets / 'current-kernel.json', dict(directory=str(destination)))
        return pair


def expose(directory, kernel, module):
    """Expose the pair for existing direct host-to-host asset streaming."""
    export = Path(directory) / 'export-assets'
    if export.exists():
        shutil.rmtree(export)
    export.mkdir()
    source = Path(kernel).parent
    _, verified_module = verify(source)
    if Path(module).resolve() != verified_module.resolve():
        raise ValueError('Cannot export a module outside its managed kernel pair')
    record = json.loads((source / 'kernel-assets.json').read_text())
    for name in (*asset_files(record.get('backend', 'dma')), 'kernel-assets.json'):
        (export / name).symlink_to((source / name).resolve(strict=True))
    return digest(source / 'kernel-assets.json')


def smoke(root, directory, image, disk, kernel, module, timeout=900, backend='dma'):
    """Boot a COW guest and load the driver before any benchmark is dispatched."""
    asset_files(backend)
    root, directory = Path(root), Path(directory)
    output = directory / 'kernel-smoke'
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    atomic_json(directory / 'stage.json', dict(phase=f'Verifying guest boot and {backend} driver'))
    name = 'chimaera-kernel-smoke-' + uuid.uuid4().hex[:12]
    gid = os.environ.get('CHIMAERA_KVM_GID') or str(Path('/dev/kvm').stat().st_gid)
    command = ['docker', 'run', '--rm', '--init', '--name', name, '--device', '/dev/kvm',
               '--group-add', gid,
               '--user', f'{os.getuid()}:{os.getgid()}', '--entrypoint', '/bin/bash']
    operation = os.environ.get('CHIMAERA_OPERATION')
    if operation:
        command += ['--label', 'chimaera.experiment=' + operation]
    for source, target in [(root / 'orchestrator/kernel', '/recipe'), (disk, '/assets/guest.img'),
                           (kernel, '/assets/kernel'), (module, '/assets/' + MODULES[backend])]:
        command += ['--mount', f'type=bind,source={source},target={target},readonly']
    expected_release = None
    pair_directory = Path(module).parent
    if (pair_directory / 'kernel-assets.json').is_file():
        pair_kernel, pair_module = verify(pair_directory, backend=backend)
        if pair_kernel.resolve() != Path(kernel).resolve() or pair_module.resolve() != Path(module).resolve():
            raise ValueError('Boot verification requires the matching managed kernel/module pair')
        expected_release = json.loads((pair_directory / 'build.json').read_text())['kernel_release']
    command += ['--mount', f'type=bind,source={output},target=/output', image, '-c',
                'exec /opt/chimaera/gem5/build/X86/gem5.opt --outdir=/output '
                '/recipe/smoke.py --image /assets/guest.img --kernel /assets/kernel '
                '--module /assets/' + MODULES[backend] + ' --backend ' + backend +
                (' --expected-release ' + shlex.quote(expected_release) if expected_release else '')]
    try:
        with (output / 'launch.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'Guest kernel/{backend} driver boot verification failed; inspect kernel-smoke logs') from error
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
    record = json.loads((output / 'result.json').read_text())
    if record.get('status') != 'passed':
        raise ValueError(f'Guest kernel/{backend} driver verification did not pass')
    if backend == 'm5ops' and (record.get('backend') != backend or record.get('abi_version') != 1 or
                             record.get('device') != '/dev/chimaera-m5' or record.get('lanes') != [0, 1]):
        raise ValueError('Guest m5ops driver verification lacks its ABI 1 lane probe')
    if expected_release is not None and record.get('kernel_release') != expected_release:
        raise ValueError('Guest booted kernel release differs from its managed module pair')
    return dict(record, kernel_sha256=digest(kernel), module_sha256=digest(module))
