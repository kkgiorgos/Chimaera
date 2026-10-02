#!/usr/bin/env python3
"""Build the shared gem5/ROS/Gazebo image, independent of benchmark files."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess


def main():
    directory = Path(__file__).resolve().parent
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--profile', default='jammy-humble-fortress')
    cli.add_argument('--jobs', type=int, default=3)
    cli.add_argument('--tag')
    cli.add_argument('--no-cache', action='store_true')
    cli.add_argument('--print-command', action='store_true')
    args = cli.parse_args()
    try:
        profile_path = directory / 'profiles' / (args.profile + '.json')
        if args.jobs < 1 or profile_path.parent != directory / 'profiles':
            raise ValueError('invalid jobs or profile')
        profile = json.loads(profile_path.read_text())
        command = ['docker', 'buildx', 'build', '--load', '--progress', 'plain',
                   '--provenance=false', '--platform', profile['platform'],
                   '--file', str(directory / 'Dockerfile'), '--tag', args.tag or 'chimaera:' + args.profile,
                   '--label', 'io.chimaera.stack=' + args.profile,
                   '--build-arg', 'BASE_IMAGE=' + profile['base_image'],
                   '--build-arg', 'STACK_PROFILE=' + args.profile,
                   '--build-arg', 'ROS_DISTRO=' + profile['ros_distro'],
                   '--build-arg', 'BUILD_JOBS=' + str(args.jobs)]
        if args.no_cache:
            command.append('--no-cache')
        command.append(str(directory.parent))
        print(shlex.join(command), flush=True)
        if not args.print_command:
            subprocess.run(command, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    main()
