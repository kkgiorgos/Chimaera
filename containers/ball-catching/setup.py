#!/usr/bin/env python3
"""Build and prepare a Docker suite without running any robot benchmark cases."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from configure import DIRECTORY, ROOT, configure, load_config, validate_limits


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True, help='New input directory, separate from results')
    cli.add_argument('--config', type=Path, default=ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json')
    cli.add_argument('--image', default='chimaera:ball-catching')
    cli.add_argument('--sdk-image', default='chimaera:jammy-humble-fortress')
    cli.add_argument('--skip-build', action='store_true', help='Use an already built/exported application image')
    cli.add_argument('--rebuild-sdk', action='store_true')
    cli.add_argument('--prebuilt-gem5', type=Path)
    cli.add_argument('--build-jobs', type=int, default=2)
    cli.add_argument('--disk', type=Path, default=ROOT / 'gem5/resources/x86-ubuntu-22.04-ros-humble.img')
    cli.add_argument('--kernel', type=Path, default=ROOT / 'gem5/resources/x86-linux-kernel-5.15.180')
    cli.add_argument('--cpus', type=float, default=4)
    cli.add_argument('--memory', default='12g')
    cli.add_argument('--calibration-from', type=Path,
                     help='Reuse a frozen calibration directory; simulator/config hashes must match')
    args = cli.parse_args()
    try:
        if args.build_jobs < 1 or args.output.exists():
            raise ValueError('Build jobs must be positive and inputs output must be new')
        load_config(args.config)
        validate_limits(args.cpus, args.memory)
        if not args.disk.is_file() or not args.kernel.is_file():
            raise ValueError('Guest disk and kernel must exist before building')
        if not args.skip_build:
            present = subprocess.run(['docker', 'image', 'inspect', args.sdk_image],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if present.returncode or args.rebuild_sdk:
                command = [sys.executable, str(DIRECTORY.parent / 'build.py'), '--tag', args.sdk_image,
                           '--jobs', str(args.build_jobs)]
                if args.prebuilt_gem5:
                    command += ['--prebuilt-gem5', str(args.prebuilt_gem5)]
                subprocess.run(command, check=True)
            subprocess.run(['docker', 'buildx', 'build', '--load', '--progress', 'plain',
                '--provenance=false', '--platform', 'linux/amd64', '-f', str(DIRECTORY / 'Dockerfile'),
                '--build-arg', 'SDK_IMAGE=' + args.sdk_image,
                '--build-arg', 'BUILD_JOBS=' + str(args.build_jobs), '-t', args.image, str(ROOT)], check=True)
        image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image], text=True).strip()
        inputs = args.output.resolve()
        configure(args.config, inputs, image, args.disk, args.kernel, args.cpus, args.memory)
        spec = importlib.util.spec_from_file_location('shared_runner', DIRECTORY.parent / 'run.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        config = runner.load(inputs / 'runner.json')
        prepared = inputs / 'prepared'
        prepared.mkdir()
        # Apply the exact worker isolation during setup, but no KVM is needed
        # for memory-only calibration or guest packaging.
        calibration = dict(config, devices=[])
        calibration['command'] = ['python3', '/app/containers/ball-catching/worker.py',
                                  '--prepare', '--output', '/output']
        if args.calibration_from:
            calibration['mounts'] = [*config['mounts'], dict(
                source=str(args.calibration_from.resolve(strict=True)), target='/assets/shared-calibration')]
            calibration['command'] += ['--calibration-from', '/assets/shared-calibration']
        job = dict(name='prepare', args=[], env={})
        import uuid
        command = runner.command(calibration, job, prepared, image, 'chimaera-ball-prepare-' + uuid.uuid4().hex[:12])
        if os.environ.get('CHIMAERA_OPERATION'):
            command[2:2] = ['--label', 'chimaera.experiment=' + os.environ['CHIMAERA_OPERATION']]
        print(shlex.join(command), flush=True)
        subprocess.run(command, check=True)
        print(f'Ready: {inputs / "runner.json"}; no robot benchmark cases were run', flush=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    main()
