#!/usr/bin/env python3
"""Build wall-follow into a mounted overlay using the shared image."""
import argparse
import os
from pathlib import Path
import subprocess
import shutil
import tempfile


def main():
    directory = Path(__file__).resolve().parent
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', default='chimaera:jammy-humble-fortress')
    cli.add_argument('--output', type=Path, default=directory / 'overlay')
    cli.add_argument('--jobs', type=int, default=3)
    args = cli.parse_args()
    if args.jobs < 1:
        cli.error('jobs must be positive')
    output = args.output.expanduser().resolve()
    workspace = directory.parents[1] / 'ros/wall_follow_ws'
    if output == workspace or output in workspace.parents or workspace in output.parents:
        cli.error('overlay output must be outside the source workspace')
    if output.exists() and any(output.iterdir()) and not (output / 'share/wall_follow_robot/package.xml').is_file():
        cli.error('refusing to replace a non-overlay directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    command = ['docker', 'run', '--rm', '--init', '--network', 'none',
               '--user', f'{os.getuid()}:{os.getgid()}', '--env', f'MAKEFLAGS=-j{args.jobs}',
               '--env', f'CMAKE_BUILD_PARALLEL_LEVEL={args.jobs}',
               '--mount', f'type=bind,source={directory.parents[1] / "ros/wall_follow_ws"},target=/benchmark,readonly',
               '--mount', f'type=bind,source={staged},target=/overlay', args.image,
               'bash', '-c', 'colcon --log-base /tmp/log build --base-paths /benchmark/src '
               '--build-base /tmp/build --merge-install --install-base /overlay --executor sequential '
               '--cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF']
    try:
        subprocess.run(command, check=True)
        if output.exists():
            shutil.rmtree(output)
        staged.rename(output)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    print(f'Benchmark overlay: {output}')


if __name__ == '__main__':
    main()
