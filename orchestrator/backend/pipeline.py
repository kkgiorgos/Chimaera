"""One detached preparation operation: build locally, freeze inputs, validate provenance."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

from common import atomic_json, build_identities, digest, source_identity


def prepare(directory):
    from guest import ensure_assets
    root = Path(os.environ['CHIMAERA_REPO'])
    request = json.loads((directory / 'request.json').read_text())
    calibration = request.get('calibration')
    checkpoint_mode = request.get('readiness_checkpoint', False)
    config = request['config']
    if config.get('fixed', {}).get('transport_backend', 'm5') == 'dma' and checkpoint_mode:
        raise ValueError('DMA requires a fresh boot; readiness checkpoints are not supported')
    dma = config.get('fixed', {}).get('transport_backend', 'm5') == 'dma'
    shared_kernel = (calibration or {}).get('provenance', {}).get('kernel_bundle_sha256')
    managed_kernel = dma and not config.get('fixed', {}).get('dma_module') and not shared_kernel
    if managed_kernel and request.get('kernel'):
        raise ValueError('A custom DMA kernel requires fixed.dma_module built for that kernel; '
                         'leave both fields empty for an automatically built pair')
    if config.get('fixed', {}).get('dma_module'):
        config['fixed']['dma_module'] = str(root / config['fixed']['dma_module'])
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
    if calibration:
        sdk_id = calibration['provenance'].get('sdk_image_id')
        application_id = calibration['provenance'].get('application_image_id')
        if not sdk_id or not application_id:
            raise ValueError('Shared calibration lacks its image identities; prepare the producer again')
        if subprocess.run(['docker', 'image', 'inspect', application_id], capture_output=True).returncode:
            raise ValueError('Calibration image is missing; complete image transfer before preparation')
        base_id = calibration['provenance'].get('base_image_id', sdk_id)
    else:
        from images import build
        sdk_id, base_id, application_id = build(root, builds, request['build_jobs'])
    image = application_id
    for image_id in (sdk_id, application_id):
        atomic_json(root / '.chimaera-orchestrator/assets' /
                    ('image-' + image_id.removeprefix('sha256:') + '.json'), dict(image_id=image_id))
    atomic_json(directory / 'stage.json', dict(phase='Preparing guest disk and kernel'))
    kernel_bundle = None
    if shared_kernel:
        from kernel_assets import verify
        guest_kernel, module = verify(directory / 'shared-assets', shared_kernel)
        config.setdefault('fixed', {})['dma_module'] = str(module)
        kernel_bundle = shared_kernel
    elif managed_kernel:
        from kernel_assets import ensure, expose
        guest_kernel, module = ensure(root, directory, request['build_jobs'])
        config.setdefault('fixed', {})['dma_module'] = str(module)
        kernel_bundle = expose(directory, guest_kernel, module)
    else:
        guest_kernel = request.get('kernel')
    if kernel_bundle:
        pair_directory = Path(config['fixed']['dma_module']).parent
        for source, target in [('kernel-assets.json', 'kernel-assets.json'),
                               ('build.json', 'kernel-build.json'), ('kernel.config', 'kernel.config')]:
            shutil.copyfile(pair_directory / source, directory / target)
    # Keep the queued config intact; resolve the managed driver only in the
    # preparation copy. configure.py freezes it under a portable /assets path.
    atomic_json(directory / 'experiment.json', config)
    readiness = None
    if checkpoint_mode and calibration:
        from checkpoints import unpack
        checkpoint_index = calibration['provenance'].get('checkpoint_index_sha256')
        if not checkpoint_index:
            raise ValueError('Shared preparation lacks its readiness checkpoint; prepare the producer again')
        readiness = unpack(directory, application_id, checkpoint_index)
        disk, kernel = readiness / 'guest.img', readiness / 'kernel'
    else:
        disk, kernel = ensure_assets(root, directory, base_id, request.get('disk'), guest_kernel)
    dma_boot = None
    if dma and not calibration:
        from kernel_assets import smoke
        dma_boot = smoke(root, directory, application_id, disk, kernel,
                         Path(config['fixed']['dma_module']),
                         timeout=config['fixed'].get('startup_timeout_s', 900))
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
                      sdk_image_id=sdk_id, base_image_id=base_id, application_image_id=application_id,
                      disk_sha256=digest(disk), kernel_sha256=digest(kernel),
                      input_hashes=hashes)
    if kernel_bundle:
        provenance['kernel_bundle_sha256'] = kernel_bundle
    if dma_boot:
        provenance['dma_boot'] = dma_boot
    if checkpoint_mode:
        from checkpoints import package
        if not calibration:
            atomic_json(directory / 'stage.json', dict(phase='Exposing readiness assets for sparse transfer'))
            application_id, checkpoint_index = package(directory, application_id, disk, kernel)
            image = application_id
            atomic_json(root / '.chimaera-orchestrator/assets' /
                ('image-' + application_id.removeprefix('sha256:') + '.json'), dict(image_id=application_id))
        provenance.update(application_image_id=application_id, readiness_checkpoint=True,
                          checkpoint_index_sha256=checkpoint_index)
        # Checkpoints are mounted once per machine, outside the application image.
        runner = json.loads((inputs / 'runner.json').read_text())
        runner.update(image=application_id, devices=[])
        atomic_json(inputs / 'runner.json', runner)
    if calibration:
        # Local ext4 images contain host-local filesystem timestamps/UUIDs.
        # Keep each disk's digest in its provenance without requiring identical bytes.
        expected = calibration['provenance']
        keys = ('source_identity', 'revision', 'kernel_sha256', 'input_hashes')
        if shared_kernel:
            keys += ('kernel_bundle_sha256',)
        if checkpoint_mode:
            keys += ('disk_sha256', 'checkpoint_index_sha256')
        for key in keys:
            if provenance[key] != expected[key]:
                raise ValueError('Prepared inputs, revision or kernel differ from the calibration machine')
        if expected.get('build_inputs') is not None and builds != expected['build_inputs']:
            raise ValueError('Simulator/application build sources differ from the calibration machine')
    atomic_json(directory / 'ready.json', dict(records=records, provenance=provenance,
        plan=json.loads((inputs / 'plan.json').read_text()), image=image))
    if not calibration:
        atomic_json(root / '.chimaera-orchestrator/assets/current-guest.json',
                    dict(disk=str(disk), kernel=str(kernel)))
    from assets import clean_unused
    clean_unused(root / '.chimaera-orchestrator', [disk, kernel])
    atomic_json(directory / 'stage.json', dict(phase='Ready'))


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
