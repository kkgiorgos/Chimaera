#!/usr/bin/env python3
"""Build the shared gem5/ROS/Gazebo image, independent of benchmark files."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def stage_gem5(root, output):
    """Export binaries and licenses without host paths or source build products."""
    root = root.resolve()
    files = {
        'gem5/build/X86/gem5.opt': root / 'build/X86/gem5.opt',
        'gem5/util/m5/build/x86/out/m5': root / 'util/m5/build/x86/out/m5',
        'gem5/util/m5/build/x86/out/libm5.a': root / 'util/m5/build/x86/out/libm5.a',
        'ramulator2/libramulator.so': root / 'ext/ramulator2/ramulator2/libramulator.so',
        'ramulator2/LICENSE': root / 'src/mem/ramulator2/LICENSE',
    }
    files.update({'ramulator2/' + path.name: path for path in (root / 'ext/ramulator2/configs').glob('*.json')})
    hashes = {}
    for name, source in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[name] = _digest(source)
    (output / 'prebuilt-gem5.json').write_text(json.dumps(dict(files=hashes), indent=2) + '\n')


def _digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    directory = Path(__file__).resolve().parent
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--profile', default='jammy-humble-fortress')
    cli.add_argument('--jobs', type=int, default=3)
    cli.add_argument('--tag')
    cli.add_argument('--no-cache', action='store_true')
    cli.add_argument('--print-command', action='store_true')
    cli.add_argument('--prebuilt-gem5', type=Path,
                     help='Reuse compatible built gem5/libm5/Ramulator artifacts instead of compiling gem5')
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
        with tempfile.TemporaryDirectory(prefix='chimaera-gem5-artifacts-') as temporary:
            if args.prebuilt_gem5:
                if not args.print_command:
                    stage_gem5(args.prebuilt_gem5, Path(temporary))
                command += ['--build-arg', 'GEM5_STAGE=gem5-prebuilt',
                            '--build-context', 'gem5-artifacts=' + temporary]
            command.append(str(directory.parent))
            print(shlex.join(command), flush=True)
            if not args.print_command:
                subprocess.run(command, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    main()
