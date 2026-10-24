"""One replaceable SDK, dependency base and application, with bounded build cache."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from common import atomic_json, digest, run


BUILDER = 'chimaera-orchestrator'
SDK = 'chimaera:orchestrator-sdk'
BASE = 'chimaera:orchestrator-base'
APPLICATION = 'chimaera:orchestrator-app'
SIMULATOR_FILES = ('gem5/build/X86/gem5.opt', 'gem5/util/m5/build/x86/out/m5',
                   'gem5/util/m5/build/x86/out/libm5.a', 'ramulator2/libramulator.so')


def simulator_artifacts(assets, key):
    """Verify the persistent simulator checkpoint, independent of BuildKit GC."""
    directory = assets / 'simulator-cache'
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        if manifest['key'] != key or not set(SIMULATOR_FILES).issubset(manifest['files']):
            return None
        actual = {str(p.relative_to(directory / 'artifacts'))
                  for p in (directory / 'artifacts').rglob('*') if p.is_file()}
        if actual != set(manifest['files']):
            return None
        for name, expected in manifest['files'].items():
            if digest(directory / 'artifacts' / name) != expected:
                return None
    except (OSError, ValueError, KeyError):
        return None
    return directory / 'artifacts'


def save_simulator(assets, key, sdk):
    """Retain installed binaries only; never preserve SDK source/build trees."""
    assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='simulator-export-', dir=assets) as temporary:
        staging = Path(temporary)
        artifacts = staging / 'artifacts'
        artifacts.mkdir()
        container = run(['docker', 'create', sdk], text=True).strip()
        try:
            for name in ('gem5', 'ramulator2'):
                subprocess.run(['docker', 'cp', container + ':/opt/chimaera/' + name,
                                str(artifacts / name)], check=True)
        finally:
            subprocess.run(['docker', 'rm', container], check=True, stdout=subprocess.DEVNULL)
        if any(not (artifacts / name).is_file() for name in SIMULATOR_FILES):
            raise ValueError('SDK is missing simulator artifacts')
        files = {str(p.relative_to(artifacts)): digest(p)
                 for p in artifacts.rglob('*') if p.is_file()}
        atomic_json(staging / 'manifest.json', dict(key=key, sdk=sdk, files=files))
        # A failed export leaves the previous checkpoint usable. Only one
        # preparation may run on a worker, so publication needs no reader lock.
        destination = assets / 'simulator-cache'
        previous = assets / 'simulator-cache-old'
        if previous.exists():
            shutil.rmtree(previous)
        if destination.exists():
            destination.rename(previous)
        staging.rename(destination)
        if previous.exists():
            shutil.rmtree(previous)
    return destination / 'artifacts'


def application_image(root, base, tag, jobs):
    """Compile in the installed base and commit only the installed overlay.

    A container build uses daemon-local bases directly and leaves no second
    BuildKit copy of each application revision or intermediate build tree.
    """
    name = 'chimaera-app-build-' + uuid.uuid4().hex[:12]
    command = ['docker', 'run', '--name', name]
    operation = os.environ.get('CHIMAERA_OPERATION')
    if operation:
        command += ['--label', 'chimaera.experiment=' + operation]
    for source, target in [('ros/franka_ws/src/franka_description', '/src/ball/franka_description'),
                           ('ros/ball_catching_ws/src', '/src/ball/packages')]:
        command += ['--mount', f'type=bind,source={root / source},target={target},readonly']
    shell = ('source /opt/ros/humble/setup.bash && source /opt/chimaera/ros/local_setup.bash && '
             f'CMAKE_BUILD_PARALLEL_LEVEL={int(jobs)} colcon --log-base /tmp/ball-log build '
             '--base-paths /src/ball --build-base /tmp/ball-build --install-base /opt/chimaera/ball '
             '--executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF && '
             'rm -rf /tmp/ball-build /tmp/ball-log')
    try:
        subprocess.run([*command, '--entrypoint', '/bin/bash', base, '-o', 'pipefail', '-c', shell], check=True)
        subprocess.run(['docker', 'commit', '--change',
            'ENV CHIMAERA_SETUP=/opt/chimaera/ball/local_setup.bash LIBGL_ALWAYS_SOFTWARE=1',
            '--change', 'ENTRYPOINT ["/bin/bash", "/opt/chimaera/scripts/entrypoint.sh"]',
            '--change', 'CMD ["/bin/bash"]', name, tag], check=True)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


def prune():
    if subprocess.run(['docker', 'buildx', 'inspect', BUILDER], capture_output=True).returncode == 0:
        run(['docker', 'buildx', 'prune', '--builder', BUILDER, '--all', '--force',
             '--keep-storage', '2GB'], timeout=600)


def build(root, builds, jobs):
    assets = root / '.chimaera-orchestrator/assets'
    marker = assets / 'current-images.json'
    cached = json.loads(marker.read_text()) if marker.exists() else {}
    current = dict(cached)
    initialized = False

    def ensure(tag, key, command):
        nonlocal initialized
        saved = cached.get(tag, {})
        present = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                                 capture_output=True, text=True)
        if present.returncode or saved.get('key') != key or saved.get('id') != present.stdout.strip():
            if not callable(command) and not initialized:
                exists = subprocess.run(['docker', 'buildx', 'inspect', BUILDER], capture_output=True)
                if exists.returncode:
                    subprocess.run(['docker', 'buildx', 'create', '--name', BUILDER,
                                    '--driver', 'docker-container'], check=True)
                initialized = True
            if callable(command):
                command()
            else:
                subprocess.run(command, check=True)
        image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
        current[tag] = dict(key=key, id=image)
        atomic_json(assets / ('image-' + image.removeprefix('sha256:') + '.json'), dict(image_id=image))
        # Persist each successfully replaced slot so failures can reuse it.
        atomic_json(marker, current)
        return image

    artifacts = simulator_artifacts(assets, builds['simulator'])
    reuse = ['--gem5-artifacts', str(artifacts)] if artifacts else []
    sdk = ensure(SDK, builds['sdk'], [sys.executable, str(root / 'containers/build.py'),
        '--tag', SDK, '--builder', BUILDER, '--jobs', str(jobs), *reuse])
    if artifacts is None:
        artifacts = save_simulator(assets, builds['simulator'], sdk)
    dockerfile = str(root / 'containers/ball-catching/Dockerfile')
    base_key = hashlib.sha256((sdk + Path(dockerfile).read_text()).encode()).hexdigest()
    # The isolated builder cannot resolve daemon-local tags. Build the base in
    # the SDK's own graph, reusing those stages without exporting an SDK archive.
    with tempfile.TemporaryDirectory(prefix='chimaera-base-') as temporary:
        combined = Path(temporary) / 'Dockerfile'
        dependencies = Path(dockerfile).read_text().split('FROM ${APPLICATION_BASE}')[0]
        dependencies = '\n'.join(line for line in dependencies.splitlines()
                                 if not line.startswith(('ARG SDK_IMAGE=', 'ARG APPLICATION_BASE=')))
        dependencies = dependencies.replace('FROM ${SDK_IMAGE} AS dependencies',
                                             'FROM image AS application-dependencies')
        combined.write_text((root / 'containers/Dockerfile').read_text() + '\n' + dependencies + '\n')
        combined.with_name('Dockerfile.dockerignore').write_text(
            (root / 'containers/Dockerfile.dockerignore').read_text())
        profile = json.loads((root / 'containers/profiles/jammy-humble-fortress.json').read_text())
        base = ensure(BASE, base_key, ['docker', 'buildx', 'build', '--builder', BUILDER,
            '--load', '--progress', 'plain', '--provenance=false', '--platform', profile['platform'],
            '-f', str(combined), '--target', 'application-dependencies',
            '--build-arg', 'BASE_IMAGE=' + profile['base_image'],
            '--build-arg', 'STACK_PROFILE=jammy-humble-fortress',
            '--build-arg', 'ROS_DISTRO=' + profile['ros_distro'],
            '--build-arg', 'GEM5_STAGE=gem5-prebuilt',
            '--build-context', 'gem5-artifacts=' + str(artifacts),
            '--build-arg', 'BUILD_JOBS=' + str(jobs), '-t', BASE, str(root)])
    key = hashlib.sha256((builds['application'] + base).encode()).hexdigest()
    application = ensure(APPLICATION, key, lambda: application_image(root, base, APPLICATION, jobs))
    if initialized:
        # This builder belongs exclusively to the orchestrator. Never prune a
        # user's default builder or the daemon's unrelated images/volumes.
        prune()
    return sdk, base, application
