#!/usr/bin/env python3
"""Prepare or execute one frozen case inside an isolated Docker worker."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / 'ros/ball_catching_ws/scripts'
sys.path.insert(0, str(SCRIPTS))
from run_memory_suite import digest


def common(inputs, output):
    record = json.loads((inputs / 'inputs.json').read_text())
    for name, expected in record['sha256'].items():
        if digest(inputs / name) != expected:
            raise ValueError('Frozen input changed: ' + name)
    return [sys.executable, str(SCRIPTS / 'run_memory_suite.py'), '--config', str(inputs / 'experiment.json'),
            '--gem5-root', '/opt/chimaera/gem5', '--image', '/assets/guest.img',
            '--kernel', '/assets/kernel', '--output', str(output),
            '--source-revision', record['source_revision']]


def prepare(inputs, output):
    """Only short memory calibration and guest packaging; no robot simulation."""
    command = common(inputs, output)
    subprocess.run(command + ['--calibrate-only'], check=True)
    data = json.loads((inputs / 'experiment.json').read_text())
    if 'stream' in data['patterns']:
        subprocess.run([sys.executable, str(SCRIPTS / 'check_aggressor_rates.py'),
            '--config', str(inputs / 'experiment.json'), '--gem5-root', '/opt/chimaera/gem5',
            '--calibration', str(output / 'calibration/stream/calibration.json'),
            '--output', str(output / 'rate-validation')], check=True)
    subprocess.run(command + ['--prepare-only', '--resume'], check=True)


def run_case(inputs, output, case):
    command = common(inputs, output)
    prepared = inputs / 'prepared'
    manifest = json.loads((prepared / 'experiment_manifest.json').read_text())
    selected = next((p for p in manifest['points'] if p['id'] == case), None)
    if selected is None:
        raise ValueError('Unknown case: ' + case)
    # Each worker has its own writable manifest/bundle and private /tmp sockets.
    output.mkdir(parents=True, exist_ok=True)
    if not (output / 'experiment_manifest.json').exists():
        if any(output.iterdir()):
            raise ValueError('Worker suite must be empty or resumable')
        shutil.copy2(prepared / 'experiment_manifest.json', output / 'experiment_manifest.json')
        shutil.copy2(prepared / 'guest.tar.gz', output / 'guest.tar.gz')
        (output / 'calibration').symlink_to(prepared / 'calibration', target_is_directory=True)
        (output / 'routes.json').write_bytes((prepared / 'routes.json').read_bytes())
    return subprocess.call(command + ['--resume', '--case', case])


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--inputs', type=Path, default=Path('/assets/inputs'))
    cli.add_argument('--output', type=Path, default=Path('/output/suite'))
    cli.add_argument('--prepare', action='store_true')
    cli.add_argument('--case')
    args = cli.parse_args()
    try:
        if args.prepare:
            if args.case:
                raise ValueError('Preparation does not select a case')
            prepare(args.inputs, args.output)
            return 0
        if not args.case:
            raise ValueError('--case is required for execution')
        return run_case(args.inputs, args.output, args.case)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
