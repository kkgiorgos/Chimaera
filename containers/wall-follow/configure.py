#!/usr/bin/env python3
"""Export a wall-follow sweep as jobs for the generic Docker runner."""

import argparse
import json
from pathlib import Path
import sys

DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(DIRECTORY.parents[1] / 'ros/wall_follow_ws/scripts'))
from run_experiments import make_plan


def configure(args):
    content = args.config.read_bytes()
    plan = make_plan(json.loads(content), args.architecture, gem5=not args.local)
    if any(run['parameters']['gui'] for run in plan['runs']):
        raise ValueError('container sweeps require gui=false')
    command = ['python3', '/assets/workspace/scripts/run_experiments.py',
               '--config', '/assets/experiment.json', '--output', '/output/suite',
               '--architecture', args.architecture, '--collect-only', '--resume']
    overlay = args.overlay.expanduser().resolve(strict=True)
    workspace = DIRECTORY.parents[1] / 'ros/wall_follow_ws'
    mounts = [dict(source=str(workspace), target='/assets/workspace'),
              dict(source=str(overlay), target='/assets/overlay')]
    devices = []
    if not args.local:
        # Setup validates once; the runner does not hash large disks per job.
        from guest_assets import selected, verify
        guest = (args.guest_assets or selected('jammy-humble-fortress')).expanduser().resolve(strict=True)
        verify(guest)
        mounts += [dict(source=str(guest / 'disk.img'), target='/assets/guest.img'),
                   dict(source=str(guest / 'kernel'), target='/assets/kernel')]
        devices.append('/dev/kvm')
        command += ['--gem5', '--gem5-root', '/opt/chimaera/gem5',
                    '--image', '/assets/guest.img', '--kernel', '/assets/kernel']
    output = args.output.expanduser().resolve()
    if output.exists():
        raise ValueError('configuration output already exists; choose a new directory')
    output.mkdir(parents=True)
    (output / 'experiment.json').write_bytes(content)
    jobs = []
    for run in plan['runs']:
        name = run['id']
        jobs.append(dict(name=name, args=['--run-id', name]))
    mounts.append(dict(source='experiment.json', target='/assets/experiment.json'))
    config = dict(image=args.image, command=command, jobs=jobs, mounts=mounts,
                  devices=devices, cpus=2, memory='8g',
                  env=dict(LIBGL_ALWAYS_SOFTWARE='1', ROS_LOCALHOST_ONLY='1',
                           IGN_PARTITION='chimaera',
                           CHIMAERA_SETUP='/assets/overlay/local_setup.bash'))
    (output / 'runner.json').write_text(json.dumps(config, indent=2) + '\n')
    print(f'Created {len(jobs)} jobs: {output / "runner.json"}')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, default=DIRECTORY.parents[1] / 'ros/wall_follow_ws/experiments/demo.json')
    cli.add_argument('--output', type=Path, required=True, help='New directory for runner config and inputs')
    cli.add_argument('--image', default='chimaera:jammy-humble-fortress')
    cli.add_argument('--overlay', type=Path, default=DIRECTORY / 'overlay')
    cli.add_argument('--architecture', default='baseline')
    cli.add_argument('--guest-assets', type=Path, help='Override the guest selected during setup')
    cli.add_argument('--local', action='store_true')
    args = cli.parse_args()
    try:
        if args.local and args.guest_assets:
            raise ValueError('--local does not accept guest assets')
        configure(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    main()
