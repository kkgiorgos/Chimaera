#!/usr/bin/env python3
"""Prepare or execute one frozen case inside an isolated Docker worker."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / 'ros/ball_catching_ws/scripts'
sys.path.insert(0, str(SCRIPTS))
from run_memory_suite import digest, write_json


def common(inputs, output):
    record = json.loads((inputs / 'inputs.json').read_text())
    for name, expected in record['sha256'].items():
        if digest(inputs / name) != expected:
            raise ValueError('Frozen input changed: ' + name)
    return [sys.executable, str(SCRIPTS / 'run_memory_suite.py'), '--config', str(inputs / 'experiment.json'),
            '--gem5-root', '/opt/chimaera/gem5', '--image', '/assets/guest.img',
            '--kernel', '/assets/kernel', '--output', str(output),
            '--source-revision', record['source_revision']]


def prepare(inputs, output, calibration_from=None, checkpoint_create=False, readiness_from=None):
    """Calibrate/package, then create or validate shared readiness checkpoints."""
    if checkpoint_create and readiness_from:
        raise ValueError('Cannot create and import readiness together')
    output.mkdir(parents=True, exist_ok=True)
    command = common(inputs, output)
    shared = ['--calibration-from', str(calibration_from)] if calibration_from else []
    def stage(phase):
        write_json(output / 'preparation.json', dict(phase=phase))
    stage('Validating shared memory calibration' if calibration_from else 'Calibrating memory')
    subprocess.run(command + ['--calibrate-only', *shared], check=True)
    data = json.loads((inputs / 'experiment.json').read_text())
    if 'stream' in data['patterns']:
        stage('Validating fractional aggressor rates')
        subprocess.run([sys.executable, str(SCRIPTS / 'check_aggressor_rates.py'),
            '--config', str(inputs / 'experiment.json'), '--gem5-root', '/opt/chimaera/gem5',
            '--calibration', str(output / 'calibration/stream/calibration.json'),
            '--output', str(output / 'rate-validation')], check=True)
    bundle = ['--bundle-from', str(readiness_from / 'guest.tar.gz')] if readiness_from else []
    stage('Freezing guest application bundle')
    subprocess.run(command + ['--prepare-only', '--resume', *bundle], check=True)
    if checkpoint_create:
        stage('Creating readiness checkpoints')
        subprocess.run(command + ['--checkpoint-create', '--resume'], check=True)
    if readiness_from:
        stage('Validating imported readiness checkpoints')
        from readiness_checkpoint import groups, provenance, validate
        manifest = json.loads((output / 'experiment_manifest.json').read_text())
        configuration = manifest['configuration']
        identity = provenance('/opt/chimaera/gem5/build/X86/gem5.opt', '/assets/kernel',
                              '/assets/guest.img', output / 'guest.tar.gz')
        for point in groups(configuration, manifest['points']).values():
            validate(readiness_from / 'checkpoints', configuration, point, identity)
    stage('Prepared inputs ready')


def run_case(inputs, output, case):
    command = common(inputs, output)
    checkpoint = os.environ.get('CHIMAERA_CHECKPOINT_ROOT')
    if checkpoint:
        if not (Path(checkpoint) / 'index.json').is_file():
            raise ValueError('Required readiness checkpoints are missing; prepare the experiment again')
        command += ['--checkpoint-from', checkpoint]
    prepared = inputs / 'prepared'
    manifest = json.loads((prepared / 'experiment_manifest.json').read_text())
    selected = next((p for p in manifest['points'] if p['id'] == case), None)
    if selected is None:
        raise ValueError('Unknown case: ' + case)
    # Each worker has a writable manifest and shares the immutable input bundle.
    output.mkdir(parents=True, exist_ok=True)
    if not (output / 'experiment_manifest.json').exists():
        if any(output.iterdir()):
            raise ValueError('Worker suite must be empty or resumable')
        shutil.copy2(prepared / 'experiment_manifest.json', output / 'experiment_manifest.json')
        (output / 'guest.tar.gz').symlink_to(prepared / 'guest.tar.gz')
        (output / 'calibration').symlink_to(prepared / 'calibration', target_is_directory=True)
        (output / 'routes.json').write_bytes((prepared / 'routes.json').read_bytes())
    return subprocess.call(command + ['--resume', '--case', case])


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--inputs', type=Path, default=Path('/assets/inputs'))
    cli.add_argument('--output', type=Path, default=Path('/output/suite'))
    cli.add_argument('--prepare', action='store_true')
    cli.add_argument('--case')
    cli.add_argument('--calibration-from', type=Path)
    cli.add_argument('--checkpoint-create', action='store_true')
    cli.add_argument('--readiness-from', type=Path)
    args = cli.parse_args()
    try:
        if args.prepare:
            if args.case:
                raise ValueError('Preparation does not select a case')
            prepare(args.inputs, args.output, args.calibration_from,
                    args.checkpoint_create, args.readiness_from)
            return 0
        if not args.case:
            raise ValueError('--case is required for execution')
        return run_case(args.inputs, args.output, args.case)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
