#!/usr/bin/env python3
"""Build and run the local ROS application in a development container."""
import argparse
import os
from pathlib import Path
import subprocess

from workflow import DIRECTORY, prepare


def command(args):
    workspace = DIRECTORY.parents[1] / 'ros/wall_follow_ws'
    argv = ['docker', 'run', '--rm', '--init', '--network', 'host',
            '--user', f'{os.getuid()}:{os.getgid()}',
            '--env', 'LIBGL_ALWAYS_SOFTWARE=1',
            '--env', 'CHIMAERA_SETUP=/overlay/local_setup.bash',
            '--mount', f'type=bind,source={workspace},target=/workspace',
            '--mount', f'type=bind,source={args.overlay.resolve()},target=/overlay,readonly',
            '--workdir', '/workspace']
    if args.shell:
        argv += ['-it']
    if not args.headless:
        display = os.environ.get('DISPLAY')
        if not display:
            raise ValueError('GUI requires DISPLAY; use --headless for server-only development')
        argv += ['--env', f'DISPLAY={display}', '--env', 'QT_X11_NO_MITSHM=1',
                 '--mount', 'type=bind,source=/tmp/.X11-unix,target=/tmp/.X11-unix,readonly']
        authority = Path(os.environ.get('XAUTHORITY', str(Path.home() / '.Xauthority')))
        if authority.is_file():
            argv += ['--env', 'XAUTHORITY=/tmp/chimaera-xauthority',
                     '--mount', f'type=bind,source={authority.resolve()},target=/tmp/chimaera-xauthority,readonly']
    argv.append(args.image)
    argv += ['bash'] if args.shell else ['ros2', 'launch', 'wall_follow_robot', 'application.launch.py',
                                       f'gui:={str(not args.headless).lower()}', *args.launch_args]
    return argv


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', default='chimaera:jammy-humble-fortress')
    cli.add_argument('--overlay', type=Path, default=DIRECTORY / 'overlay')
    cli.add_argument('--jobs', type=int, default=3)
    cli.add_argument('--headless', action='store_true')
    cli.add_argument('--shell', action='store_true', help='Open an interactive development shell')
    cli.add_argument('--rebuild-image', action='store_true')
    cli.add_argument('launch_args', nargs='*', help='Optional ROS launch arguments, e.g. lidar_hz:=10')
    args = cli.parse_args()
    try:
        if args.jobs < 1:
            raise ValueError('jobs must be positive')
        argv = command(args)
        prepare(args.image, args.overlay, args.jobs, args.rebuild_image)
        return subprocess.call(argv)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
