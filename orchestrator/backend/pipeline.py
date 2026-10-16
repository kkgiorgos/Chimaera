"""One detached preparation operation: build locally, freeze inputs, validate provenance."""
import hashlib
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
    calibration = request.get('calibration')
    checkpoint_mode = request.get('readiness_checkpoint', False)
    config = request['config']
    # The stored JSON remains portable; resolve its repository-relative memory config here.
    config['ramulator_config'] = str(root / config['ramulator_config'])
    atomic_json(directory / 'experiment.json', config)
    identity = source_identity(root)
    revision = identity['revision']
    if identity != request['source_identity']:
        expected = request['source_identity']
        differences = '; '.join(
            f'{key}: queued={expected.get(key)}, local={identity.get(key)}'
            for key in sorted(expected.keys() | identity.keys())
            if expected.get(key) != identity.get(key))
        raise ValueError('Repository revision or source contents differ from the queued experiment: '
            + differences + '. This check uses the worker checkout, not the transferred Docker images. '
            'Restore the queued revision and source contents before retrying preparation, or stop this '
            'experiment, align the controller and worker checkouts, redeploy, and queue a new experiment. '
            'Retrying or redeploying alone does not update the frozen source identity.')
    atomic_json(directory / 'stage.json', dict(phase='Building SDK and application'))
    builds = build_identities(root)
    if calibration:
        expected_builds = calibration['provenance'].get('build_inputs')
        if expected_builds is not None and builds != expected_builds:
            differences = '; '.join(
                f'{key}: calibration={expected_builds.get(key)}, local={builds[key]}'
                for key in builds if builds[key] != expected_builds.get(key))
            raise ValueError('Simulator/application build sources differ from the calibration machine: '
                + differences + '. Check ignored external sources and executable permissions as well as Git revision. '
                'Use matching build inputs on every machine before retrying preparation.')
    sdk = 'chimaera:sdk-' + builds['sdk'][:16]
    if calibration:
        sdk = calibration['provenance'].get('sdk_image_id')
        if not sdk:
            raise ValueError('Shared calibration lacks its SDK image identity; prepare the calibration machine again')
    present = subprocess.run(['docker', 'image', 'inspect', sdk], capture_output=True)
    if present.returncode:
        if calibration:
            raise ValueError('Calibration SDK image is missing; complete SDK transfer before preparation')
        subprocess.run([sys.executable, str(root / 'containers/build.py'), '--tag', sdk, '--jobs', str(request['build_jobs'])], check=True)
    sdk_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', sdk], text=True).strip()
    if calibration and sdk_id != sdk:
        raise ValueError('Local SDK image differs from the calibration SDK')
    sdk_reference = 'chimaera:sdk-image-' + sdk_id.removeprefix('sha256:')
    subprocess.run(['docker', 'image', 'tag', sdk_id, sdk_reference], check=True)
    # A source key alone can reuse an application built against another SDK's live APT packages.
    application_key = hashlib.sha256((builds['application'] + sdk_id).encode()).hexdigest()
    image = 'chimaera:ball-' + application_key[:16]
    shared_application = calibration and calibration['provenance'].get('application_image_id')
    if shared_application:
        image = shared_application
    present = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True)
    if present.returncode:
        if shared_application:
            raise ValueError('Calibration application image is missing; complete image transfer before preparation')
        subprocess.run(['docker', 'buildx', 'build', '--load', '--progress', 'plain', '--provenance=false',
            '--platform', 'linux/amd64', '-f', str(root / 'containers/ball-catching/Dockerfile'),
            '--build-arg', 'SDK_IMAGE=' + sdk_reference, '--build-arg', 'BUILD_JOBS=' + str(request['build_jobs']),
            '-t', image, str(root)], check=True)
    application_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
    if shared_application and application_id != shared_application:
        raise ValueError('Local application image differs from the calibration application image')
    for image_id in (sdk_id, application_id):
        atomic_json(root / '.chimaera-orchestrator/assets' /
                    ('image-' + image_id.removeprefix('sha256:') + '.json'), dict(image_id=image_id))
    atomic_json(directory / 'stage.json', dict(phase='Preparing guest disk and kernel'))
    readiness = None
    if checkpoint_mode and calibration:
        from checkpoints import unpack
        checkpoint_index = calibration['provenance'].get('checkpoint_index_sha256')
        if not checkpoint_index:
            raise ValueError('Shared preparation lacks its readiness checkpoint; prepare the producer again')
        readiness = unpack(directory, application_id, checkpoint_index)
        disk, kernel = readiness / 'guest.img', readiness / 'kernel'
    else:
        disk, kernel = ensure_assets(root, directory, image, request.get('disk'), request.get('kernel'))
    args = []
    if calibration:
        for pattern, record in calibration['records'].items():
            if pattern not in ('stream', 'stride', 'random'):
                raise ValueError('Invalid calibration pattern')
            atomic_json(directory / 'shared-calibration' / pattern / 'calibration.json', record)
        args = ['--calibration-from', str(directory / 'shared-calibration')]
    if checkpoint_mode:
        args += ['--readiness-from', str(readiness)] if readiness else ['--checkpoint-create']
    phase = 'Calibrating and freezing inputs' if not calibration else 'Validating shared calibration and freezing inputs'
    if checkpoint_mode:
        phase = ('Calibrating and creating readiness checkpoints' if not calibration
                 else 'Validating shared calibration and readiness checkpoints')
    atomic_json(directory / 'stage.json', dict(phase=phase))
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
    provenance = dict(source_identity=identity, revision=revision, build_inputs=builds,
                      sdk_image_id=sdk_id, application_image_id=application_id,
                      disk_sha256=digest(disk), kernel_sha256=digest(kernel),
                      input_hashes=hashes)
    if checkpoint_mode:
        from checkpoints import LOCATION, package
        if not calibration:
            atomic_json(directory / 'stage.json', dict(phase='Packaging ready checkpoint and backing disk'))
            application_id, checkpoint_index = package(directory, application_id, disk, kernel)
            image = application_id
            atomic_json(root / '.chimaera-orchestrator/assets' /
                ('image-' + application_id.removeprefix('sha256:') + '.json'), dict(image_id=application_id))
        provenance.update(application_image_id=application_id, readiness_checkpoint=True,
                          checkpoint_index_sha256=checkpoint_index)
        # Both producer and consumers execute the immutable prepared image.
        runner = json.loads((inputs / 'runner.json').read_text())
        runner.update(image=application_id, devices=[])
        runner['env']['CHIMAERA_CHECKPOINT_ROOT'] = LOCATION + '/checkpoints'
        runner['mounts'] = [mount for mount in runner['mounts'] if mount['target'] != '/assets/readiness']
        atomic_json(inputs / 'runner.json', runner)
    if calibration:
        # Local ext4 images contain host-local filesystem timestamps/UUIDs.
        # Keep each disk's digest in its provenance without requiring identical bytes.
        expected = calibration['provenance']
        keys = ('source_identity', 'revision', 'kernel_sha256', 'input_hashes')
        if checkpoint_mode:
            keys += ('disk_sha256', 'checkpoint_index_sha256')
        for key in keys:
            if provenance[key] != expected[key]:
                raise ValueError('Prepared inputs, revision or kernel differ from the calibration machine')
        if expected.get('build_inputs') is not None and builds != expected['build_inputs']:
            raise ValueError('Simulator/application build sources differ from the calibration machine')
    atomic_json(directory / 'ready.json', dict(records=records, provenance=provenance,
        plan=json.loads((inputs / 'plan.json').read_text()), image=image))
    atomic_json(directory / 'stage.json', dict(phase='Ready'))


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
