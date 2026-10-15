"""One detached preparation operation: build locally, freeze inputs, validate provenance."""
import json
import os
from pathlib import Path
import subprocess
import sys

from common import atomic_json, build_identities, digest, run, source_identity


def prepare(directory):
    from guest import ensure_assets
    root = Path(os.environ['CHIMAERA_REPO'])
    request = json.loads((directory / 'request.json').read_text())
    config = request['config']
    # The stored JSON remains portable; resolve its repository-relative memory config here.
    config['ramulator_config'] = str(root / config['ramulator_config'])
    atomic_json(directory / 'experiment.json', config)
    identity = source_identity(root)
    revision = identity['revision']
    if identity != request['source_identity']:
        raise ValueError('Repository revision or source contents differ from the queued experiment')
    atomic_json(directory / 'stage.json', dict(phase='Building SDK and application'))
    builds = build_identities(root)
    sdk, image = 'chimaera:sdk-' + builds['sdk'][:16], 'chimaera:ball-' + builds['application'][:16]
    present = subprocess.run(['docker', 'image', 'inspect', sdk], capture_output=True)
    if present.returncode:
        subprocess.run([sys.executable, str(root / 'containers/build.py'), '--tag', sdk, '--jobs', str(request['build_jobs'])], check=True)
    present = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True)
    if present.returncode:
        subprocess.run(['docker', 'buildx', 'build', '--load', '--progress', 'plain', '--provenance=false',
            '--platform', 'linux/amd64', '-f', str(root / 'containers/ball-catching/Dockerfile'),
            '--build-arg', 'SDK_IMAGE=' + sdk, '--build-arg', 'BUILD_JOBS=' + str(request['build_jobs']),
            '-t', image, str(root)], check=True)
    atomic_json(directory / 'stage.json', dict(phase='Preparing guest disk and kernel'))
    calibration = request.get('calibration')
    disk, kernel = ensure_assets(root, directory, image, request.get('disk'), request.get('kernel'))
    args = []
    if calibration:
        for pattern, record in calibration['records'].items():
            if pattern not in ('stream', 'stride', 'random'):
                raise ValueError('Invalid calibration pattern')
            atomic_json(directory / 'shared-calibration' / pattern / 'calibration.json', record)
        args = ['--calibration-from', str(directory / 'shared-calibration')]
    atomic_json(directory / 'stage.json', dict(phase='Calibrating and freezing inputs' if not calibration else 'Validating shared calibration and freezing inputs'))
    subprocess.run([sys.executable, str(root / 'containers/ball-catching/setup.py'), '--skip-build', '--image', image,
        '--config', str(directory / 'experiment.json'), '--output', str(directory / 'inputs'),
        '--disk', str(disk), '--kernel', str(kernel), '--cpus', str(request['cpus']),
        '--memory', request['memory'], *args], check=True)
    inputs = directory / 'inputs'
    records = {path.parent.name: json.loads(path.read_text()) for path in (inputs / 'prepared/calibration').glob('*/calibration.json')}
    hashes = json.loads((inputs / 'inputs.json').read_text())['sha256']
    # The source copy contains this host's resolved memory path. The exported
    # experiment and memory.json are portable and remain strictly hashed.
    hashes.pop('source-experiment.json', None)
    provenance = dict(source_identity=identity, revision=revision, build_inputs=builds, disk_sha256=digest(disk), kernel_sha256=digest(kernel),
                      input_hashes=hashes)
    if calibration:
        # Local ext4 images contain host-local filesystem timestamps/UUIDs.
        # Keep each disk's digest in its provenance without requiring identical bytes.
        expected = calibration['provenance']
        for key in ('source_identity', 'revision', 'kernel_sha256', 'input_hashes'):
            if provenance[key] != expected[key]:
                raise ValueError('Prepared inputs, revision or kernel differ from the calibration machine')
        if expected.get('build_inputs') is not None and builds != expected['build_inputs']:
            raise ValueError('Simulator/application build sources differ from the calibration machine')
    atomic_json(directory / 'ready.json', dict(records=records, provenance=provenance,
        plan=json.loads((inputs / 'plan.json').read_text()), image=image))
    atomic_json(directory / 'stage.json', dict(phase='Ready'))


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
