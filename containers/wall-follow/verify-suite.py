#!/usr/bin/env python3
"""Verify real parallel suite execution, comparison coverage, resume, and cleanup."""

import argparse
import csv
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import worker
from guest_assets import sha256

DIRECTORY = Path(__file__).resolve().parent


def inspect(output):
    result = worker.docker('container', 'inspect', worker.container_name(output), check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def wait_running(process, outputs, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        values = [inspect(output) for output in outputs]
        if all(value and value['State']['Running'] for value in values):
            still_running = inspect(outputs[0])
            if still_running and still_running['State']['Running']:
                return values
        time.sleep(.25)
    raise RuntimeError('Two workers did not become live; inspect suite.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Verification directory; fresh unless --resume')
    parser.add_argument('--image', default='chimaera-worker:jammy-humble-fortress')
    parser.add_argument('--resume', action='store_true', help='Reuse a prior verification suite and retry remaining checks')
    parser.add_argument('--gem5', action='store_true')
    parser.add_argument('--overload', action='store_true', help='Exercise 100 MHz guests with one or two cores past the former queue overflow')
    parser.add_argument('--guest-assets', type=Path)
    rendering = parser.add_mutually_exclusive_group()
    rendering.add_argument('--render-device', type=Path, default=Path('/dev/dri/renderD128'))
    rendering.add_argument('--software-rendering', action='store_true')
    args = parser.parse_args()
    if args.overload:
        args.gem5 = True
    if args.gem5 and not args.guest_assets:
        parser.error('--gem5 requires --guest-assets')
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=args.resume)
    image = json.loads(worker.docker('image', 'inspect', args.image).stdout)[0]['Id']
    configuration = dict(repetitions=1 if args.gem5 else 2,
        fixed=dict(duration=3. if args.overload else .4 if args.gem5 else 3., wall_timeout=900. if args.gem5 else 90.,
                   gui=False, control_hz=10., lidar_hz=10., lidar_samples=720, noise_std=0.,
                   arena_width=4., arena_height=4.),
        sweep=dict(cpu_clock=['2GHz', '3GHz']) if args.gem5 else dict(speed=[.4, .7]))
    if args.overload:
        configuration['fixed'].update(cpu_clock='100MHz', control_hz=5.)
        configuration['sweep'] = dict(num_cores=[1, 2])
    config = root / 'config.json'
    config_content = json.dumps(configuration, indent=2)
    if args.resume:
        if config.read_text() != config_content:
            parser.error('verification configuration differs from the saved run')
    else:
        config.write_text(config_content)
    base = [sys.executable, str(DIRECTORY / 'run-suite.py'), '--image', image, '--workers', '2',
            '--architecture', 'verification', '--warmup', '0']
    base += ['--software-rendering'] if args.software_rendering else ['--render-device', str(args.render_device)]
    command = [*base, '--config', str(config), '--output', str(root / 'suite')]
    command += ['--guest-assets', str(args.guest_assets.resolve())] if args.gem5 else ['--local']
    if args.resume:
        command.append('--resume')
    disk = args.guest_assets.resolve() / 'disk.img' if args.gem5 else None
    disk_hash = sha256(disk) if disk else None
    evidence = dict(image_id=image, mode='gem5' if args.gem5 else 'local', command=command,
                    orchestrator_sha256=sha256(DIRECTORY / 'run-suite.py'),
                    resumed_verification=args.resume)
    active = []
    print(f'Verifying parallel suite; evidence: {root}', flush=True)
    try:
        with (root / 'suite.log').open('a' if args.resume else 'w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            active.append(process)
            first_ids = ['case_001_rep_01', 'case_002_rep_01'] if args.gem5 else ['case_001_rep_01', 'case_001_rep_02']
            outputs = [root / 'suite/jobs' / run_id for run_id in first_ids]
            if args.resume:
                snapshots = json.loads((root / 'parallel-docker.json').read_text())
            else:
                snapshots = wait_running(process, outputs)
                (root / 'parallel-docker.json').write_text(json.dumps(snapshots, indent=2))
            assert all(value['Config']['Image'] == image for value in snapshots)
            for value, output in zip(snapshots, outputs):
                host = value['HostConfig']
                assert host['NetworkMode'] == 'none' and host['IpcMode'] == 'private'
                assert host['ReadonlyRootfs'] and not host['Privileged']
                writes = [m for m in value['Mounts'] if m['Type'] == 'bind' and m['RW']]
                assert len(writes) == 1 and Path(writes[0]['Source']) == output
                assert all(not m['RW'] for m in value['Mounts'] if m['Destination'].startswith('/assets/'))
            assert process.wait(timeout=1800 if args.gem5 else 180) == 0
        state = json.loads((root / 'suite/orchestration.json').read_text())
        assert state['status'] == 'completed' and state['exit_code'] == 0
        expected = {f'case_{case:03d}_rep_{rep:02d}' for case in (1, 2)
                    for rep in range(1, configuration['repetitions'] + 1)}
        assert set(state['coverage']['selected']) == expected
        assert not state['coverage']['missing'] and not state['coverage']['duplicates'] and not state['coverage']['errors']
        assert all(job['run_ids'] == [job['name']] for job in state['jobs'])
        assert all(job['exit_code'] == 0 for job in state['jobs'])
        outputs = [root / 'suite/jobs' / job['name'] for job in state['jobs']]
        container_ids = [next((output / 'launches').glob('*/container.id')).read_text().strip() for output in outputs]
        assert len(set(container_ids)) == len(outputs)
        evidence['distinct_run_containers'] = container_ids
        comparison = root / 'suite/comparison'
        per_run = json.loads((comparison / 'per_run_summary.json').read_text())
        groups = json.loads((comparison / 'summary.json').read_text())
        assert len(per_run) == len(expected) and len(groups) == 2
        assert all(group['repetitions'] == configuration['repetitions'] for group in groups)
        assert {run['path'] for run in per_run} == set(state['coverage']['selected'].values())
        evidence['runs'] = []
        for run in per_run:
            path = Path(run['path'])
            with (path / 'samples.csv').open() as stream:
                samples = list(csv.DictReader(stream))
            with (path / 'poses.csv').open() as stream:
                poses = list(csv.DictReader(stream))
            assert len(samples) >= 3 and len(poses) >= 2
            assert len({(row['x'], row['y']) for row in poses}) > 1
            if args.gem5:
                assert run['metrics']['gem5_cycles'] > 0 and run['metrics']['gem5_instructions'] > 0
            evidence['runs'].append(dict(path=str(path), samples=len(samples), poses=len(poses)))
        for name in ('dashboard.html', 'comparison.png', 'metrics.png', 'timing.png',
                     'configuration.png', 'summary.csv', 'configuration.csv', 'coverage.json'):
            assert (comparison / name).stat().st_size > 0
        assert all(inspect(output) is None for output in outputs)
        before = {str(path.relative_to(root)): sha256(path)
                  for path in (root / 'suite/jobs').rglob('samples.csv')}
        launches_before = sorted(str(path) for path in (root / 'suite/jobs').glob('*/launches/*'))
        with (root / 'resume.log').open('a' if args.resume else 'w') as log:
            assert subprocess.run([*command, '--resume'], stdout=log, stderr=subprocess.STDOUT, timeout=180).returncode == 0
        after = {str(path.relative_to(root)): sha256(path)
                 for path in (root / 'suite/jobs').rglob('samples.csv')}
        assert before == after
        assert launches_before == sorted(str(path) for path in (root / 'suite/jobs').glob('*/launches/*'))
        assert all(inspect(output) is None for output in outputs)
        evidence['resume_samples_unchanged'] = True
        if disk:
            assert sha256(disk) == disk_hash
            evidence['guest_disk_sha256_unchanged'] = disk_hash
        interrupted_config = root / 'interrupted.json'
        cancel_fixed = {key: value for key, value in configuration['fixed'].items() if key != 'cpu_clock'}
        interrupted_config.write_text(json.dumps(dict(repetitions=4,
            fixed=dict(cancel_fixed, duration=60., wall_timeout=180.))))
        cancel = [*base, '--config', str(interrupted_config), '--local', '--output', str(root / 'interrupted')]
        if args.resume and (root / 'interrupted/orchestration.json').is_file():
            cancel.append('--resume')
        with (root / 'interrupted.log').open('a' if args.resume else 'w') as log:
            process = subprocess.Popen(cancel, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            active.append(process)
            outputs = [root / 'interrupted/jobs' / f'case_001_rep_{index:02d}' for index in (1, 2)]
            wait_running(process, outputs)
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=180) == 130
        interrupted = json.loads((root / 'interrupted/orchestration.json').read_text())
        assert interrupted['status'] == 'interrupted'
        assert all(job['status'] == 'queued' for job in interrupted['jobs'][2:])
        assert all(not (root / 'interrupted/jobs' / job['name']).exists() for job in interrupted['jobs'][2:])
        assert not (root / 'interrupted/comparison').exists()
        assert all(inspect(output) is None for output in outputs)
        evidence['interrupted_workers_removed'] = True
        (root / 'verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(f'Verified {len(expected)} runs, two concurrent workers, comparison, resume and cancellation', flush=True)
        return 0
    finally:
        for process in active:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=180)


if __name__ == '__main__':
    raise SystemExit(main())
