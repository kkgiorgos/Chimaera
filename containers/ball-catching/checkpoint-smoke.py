#!/usr/bin/env python3
"""Validate real checkpoint capture, collection, sparse asset transfer and repeated restore.

Run on a KVM-capable worker with its built application image, disk and kernel.
Uses only an isolated output directory and disposable containers. Each simulated
phase has a 30 ms detailed-time budget and a 180-second wall watchdog. No queued
experiment or supervisor database is changed.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / 'orchestrator/backend'))
from common import atomic_json, digest
from checkpoints import LOCATION, package, sparse_copy, unpack
from agent import Agent
from controller import safe_extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=SOURCE)
    parser.add_argument('--image', required=True)
    parser.add_argument('--disk', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restores', type=int, default=2)
    parser.add_argument('--skip-packaging', action='store_true',
        help='Run capture, repeated restores and report collection without transferring readiness assets')
    args = parser.parse_args()
    root, output = args.repo.resolve(), args.output.resolve()
    if output.exists() or args.restores < 2:
        parser.error('Use a new output directory and at least two restores')
    disk, kernel = args.disk.resolve(strict=True), args.kernel.resolve(strict=True)
    output.mkdir(parents=True)
    started = time.monotonic()
    phases = []

    def phase(name, action):
        begin = time.monotonic()
        print('Starting ' + name, flush=True)
        atomic_json(output / 'smoke-progress.json', dict(phase=name, status='running', phases=phases,
            elapsed_seconds=time.monotonic() - started))
        limit = 600 if name == 'package' else 300
        def timed_out(signum, frame):
            raise TimeoutError(f'{name} exceeded its {limit}-second wall watchdog')
        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(limit)
        try:
            action()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        phases.append(dict(name=name, wall_seconds=time.monotonic() - begin))
        atomic_json(output / 'smoke-progress.json', dict(phase=name, status='passed', phases=phases,
            elapsed_seconds=time.monotonic() - started))
        print(f'Passed {name}: {phases[-1]["wall_seconds"]:.1f}s', flush=True)

    def container(name, image, mode, nonce=1, imported=None, prepared_snapshot=None):
        container_name = 'chimaera-smoke-' + output.name + '-' + name
        command = ['docker', 'run', '--rm', '--name', container_name,
            '--label', 'chimaera.checkpoint-smoke=' + output.name,
            '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
            '--mount', f'type=bind,source={root},target=/repo,readonly',
            '--mount', f'type=bind,source={output},target={output}']
        # An exported source overlay lets this test validate uncommitted fixes
        # without changing the worker checkout or its frozen experiments.
        if SOURCE != root:
            for relative in ('orchestrator/backend', 'containers/ball-catching',
                'ros/ball_catching_ws/tests', 'ros/ball_catching_ws/scripts',
                'ros/chimaera_ros_ws/src/chimaera_ros_bridge'):
                command += ['--mount', f'type=bind,source={SOURCE / relative},target=/repo/{relative},readonly']
        if mode == 'capture':
            command += ['--device', '/dev/kvm', '--group-add', str(Path('/dev/kvm').stat().st_gid)]
        if prepared_snapshot:
            command += ['--mount', f'type=bind,source={prepared_snapshot},target={LOCATION},readonly']
        asset_disk = imported / 'guest.img' if imported else disk
        asset_kernel = imported / 'kernel' if imported else kernel
        command += ['--mount', f'type=bind,source={asset_disk},target=/assets/guest.img,readonly',
                    '--mount', f'type=bind,source={asset_kernel},target=/assets/kernel,readonly']
        inner = ['python3', '/repo/ros/ball_catching_ws/tests/checkpoint_smoke.py', mode,
                 '--output', str(output), '--nonce', str(nonce)]
        if mode == 'restore':
            inner += ['--readiness', LOCATION]
        import shlex
        shell = ('source /opt/ros/humble/setup.bash && '
                 'source /opt/chimaera/ros/local_setup.bash && '
                 'source /opt/chimaera/ball/local_setup.bash && exec ' + shlex.join(inner))
        command += ['--entrypoint', '/bin/bash', image, '-c', shell]
        try:
            with (output / (name + '.log')).open('w') as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True,
                               timeout=240 if mode != 'build' else 300)
        finally:
            subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True)

    try:
        phase('build', lambda: container('build', args.image, 'build'))
        phase('capture', lambda: container('capture', args.image, 'capture'))
        prepared = output / 'inputs/prepared'
        if not (prepared / 'checkpoints/index.json').is_file():
            raise RuntimeError('Capture did not freeze the checkpoint index')
        image, index, imported = args.image, digest(prepared / 'checkpoints/index.json'), None
        def image_package():
            nonlocal image, index
            image, index = package(output, args.image, disk, kernel)
        def image_import():
            nonlocal imported
            destination = output / 'consumer/shared-assets'
            destination.mkdir(parents=True)
            sparse_copy(['tar', '--dereference', '--sparse', '--format=pax', '--sparse-version=1.0',
                         '-cf', '-', '-C', str(output / 'export-assets'), '.'], destination)
            imported = unpack(output / 'consumer', image, index)
        # Corruption must fail closed before a simulator starts.
        def reject_changed_index():
            path = imported / 'checkpoints/index.json'
            original = path.read_bytes()
            try:
                path.write_bytes(original + b' ')
                try:
                    unpack(output / 'consumer', image, index)
                except ValueError:
                    return
                raise RuntimeError('Changed checkpoint index was accepted')
            finally:
                path.write_bytes(original)
        if not args.skip_packaging:
            # Catch simulator/guest failures before spending time exporting a disk.
            phase('restore-native', lambda: container('restore-native', args.image, 'restore',
                nonce=args.restores + 2, prepared_snapshot=prepared))
            phase('package', image_package)
            phase('import', image_import)
            phase('reject-corruption', reject_changed_index)
        for nonce in range(2, args.restores + 2):
            phase('restore-' + str(nonce), lambda n=nonce: container('restore-' + str(n),
                image, 'restore', nonce=n, imported=imported,
                prepared_snapshot=prepared if args.skip_packaging else imported))
        def collect():
            task = dict(id='checkpoint-smoke-prepare', kind='prepare', status='completed',
                        output=str(output), container='none')
            agent = Agent(root, output / 'isolated-agent')
            agent.preparation_report(task)
            if task.get('log_error'):
                raise RuntimeError(task['log_error'])
            archive = output / 'preparation-report.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                stream.add(output / 'preparation-report', arcname='.')
            safe_extract(archive, output / 'collected-report')
            if not list((output / 'collected-report/checkpoints').glob('*/checkpoint.json')):
                raise RuntimeError('Collected report lacks checkpoint metadata')
        phase('collect', collect)
        summary = dict(status='passed', packaged=not args.skip_packaging,
            phases=phases, image=image, index_sha256=index,
            capture=json.loads((output / 'capture-1.json').read_text()),
            native_restore=None if args.skip_packaging else
                json.loads((output / f'restore-{args.restores + 2}.json').read_text()),
            restores=[json.loads((output / f'restore-{nonce}.json').read_text())
                      for nonce in range(2, args.restores + 2)],
            elapsed_seconds=time.monotonic() - started,
            guest_bridge_sha256=digest(output / 'overlay/lib/chimaera_ros_bridge/guest_bridge'))
        atomic_json(output / 'smoke-result.json', summary)
        print(json.dumps(summary, indent=2), flush=True)
    except BaseException as error:
        atomic_json(output / 'smoke-result.json', dict(status='failed', error=str(error),
            phases=phases, elapsed_seconds=time.monotonic() - started))
        raise


if __name__ == '__main__':
    main()
