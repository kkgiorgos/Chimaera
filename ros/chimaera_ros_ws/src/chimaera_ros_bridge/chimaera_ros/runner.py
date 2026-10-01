"""Process supervision, reviewable plans, and relocatable guest staging."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
from .manifest import load


def prefix():
    from ament_index_python.packages import get_package_prefix
    return Path(get_package_prefix('chimaera_ros_bridge'))


def plan(data, side, package_prefix=None, simulate=False):
    package_prefix = Path(package_prefix) if package_prefix else prefix()
    config = data[side]
    env = dict(config['env'], ROS_DOMAIN_ID=str(config['domain_id']), ROS_LOCALHOST_ONLY='1')
    if side == 'guest':
        env.setdefault('ROS_LOG_DIR', '/tmp/chimaera-guest-logs')
    parameters = {'config_file': data['routes'], **{key: data['bridge'][key] for key in
                  ('max_serialized_bytes', 'max_pending_messages')}}
    if side == 'host':
        parameters.update(data['bridge'])
    args = [str(package_prefix / 'lib/chimaera_ros_bridge' / (side + '_bridge')), '--ros-args']
    for key, value in parameters.items():
        args += ['-p', f'{key}:={str(value).lower() if isinstance(value, bool) else value}']
    processes = [{'name': side + '_bridge', 'command': args}, *config['processes']]
    if simulate:
        sim = data.get('simulator')
        if not sim:
            raise ValueError("bringup requires simulator.gem5_root")
        root = Path(sim['gem5_root'])
        command = [str(root / 'build/X86/gem5.opt'),
                   '--outdir=' + sim.get('outdir', str(Path.cwd() / 'm5out-chimaera')),
                   str(package_prefix / 'share/chimaera_ros_bridge/config/gem5_ros.py'),
                   '--gem5-root', str(root), '--socket-path', data['bridge']['timing_socket'],
                   '--guest-command', data['deploy']['guest_root'] + '/guest_start']
        for key in ('image', 'kernel'):
            if key in sim:
                command += ['--' + key, sim[key]]
        processes.append({'name': 'gem5', 'command': command})
    return dict(side=side, setup=config['setup'], env=env, processes=processes)


def supervise(spec):
    processes = []
    interrupted = False
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    result = 0
    try:
        for item in spec['processes']:
            script = 'set -e\n' + ''.join('source ' + shlex.quote(p) + '\n' for p in spec['setup'])
            # Apply domain isolation after sourcing arbitrary application overlays.
            script += 'exec env ' + ' '.join(shlex.quote(k + '=' + v) for k, v in spec['env'].items())
            script += ' ' + shlex.join(item['command'])
            print('Starting ' + item['name'] + ': ' + shlex.join(item['command']), flush=True)
            child = subprocess.Popen(['bash', '-c', script], start_new_session=True)
            processes.append((item['name'], child))
        while not interrupted:
            exited = next(((name, p.returncode) for name, p in processes if p.poll() is not None), None)
            if exited:
                print(f'{exited[0]} exited ({exited[1]}); stopping session', flush=True)
                result = exited[1] if exited[1] >= 0 else 128 - exited[1]
                break
            time.sleep(0.1)
        if interrupted:
            result = 130
    finally:
        # Every command owns a process group, including ros2 launch descendants.
        for _, process in processes:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        while any(p.poll() is None for _, p in processes) and time.monotonic() < deadline:
            time.sleep(0.1)
        for _, process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return result


def stage(data, output, package_prefix=None):
    """Stage a guest-root tree without mounting or changing a disk image."""
    package_prefix = Path(package_prefix) if package_prefix else prefix()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Stage output must not exist: " + str(output))
    binary = package_prefix / 'lib/chimaera_ros_bridge/guest_bridge'
    if not binary.is_file():
        raise ValueError("Build the guest bridge before staging")
    sources = [package_prefix, *[Path(t['source']) for t in data['deploy']['install_trees']]]
    for source in sources:
        if not source.is_dir() or source == output or source in output.parents:
            raise ValueError("Invalid staging source or output nested in source: " + str(source))
    # Stage only the universal runtime, rather than a host workspace and its underlays.
    root = output / data['deploy']['guest_root'].lstrip('/')
    runtime = root / 'runtime'
    runtime.mkdir(parents=True)
    shutil.copy2(binary, runtime / 'guest_bridge')
    module = Path(__file__).parent
    shutil.copytree(module, runtime / 'chimaera_ros', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(data['routes'], root / 'routes.json')
    guest = json.loads(json.dumps(data))
    guest['routes'] = 'routes.json'
    guest.pop('simulator', None)
    guest.pop('builds', None)
    guest['deploy']['install_trees'] = []
    for tree in data['deploy']['install_trees']:
        # Dereference colcon symlink-install links; no host source links in the image.
        shutil.copytree(tree['source'], root / tree['destination'], symlinks=False)
    (root / 'session.json').write_text(json.dumps(guest, indent=2) + '\n')
    guest_root = data['deploy']['guest_root']
    script = '#!/usr/bin/env bash\nset -e\ncd ' + shlex.quote(guest_root) + '\n'
    script += 'export PYTHONPATH=' + shlex.quote(guest_root + '/runtime') + '\n'
    # Runner uses its own staged executable without a ROS package index lookup.
    script += 'exec python3 -m chimaera_ros.runner run session.json --side guest --guest-binary '
    script += shlex.quote(guest_root + '/runtime/guest_bridge') + '\n'
    (root / 'guest_start').write_text(script)
    (root / 'guest_start').chmod(0o755)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'plan', 'run', 'bringup', 'build', 'stage', 'deploy'))
    parser.add_argument('manifest')
    parser.add_argument('--side', choices=('host', 'guest'), default='host')
    parser.add_argument('--output', help='New directory for guest-root staging')
    parser.add_argument('--image', help='Offline raw guest image for deploy')
    parser.add_argument('--partition', type=int, default=2)
    parser.add_argument('--guest-binary', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        data = load(args.manifest)
        if args.action in ('validate', 'stage'):
            result = subprocess.call([str(prefix() / 'lib/chimaera_ros_bridge/validate_routes'), data['routes']])
            if result or args.action == 'validate':
                return result
        if args.action == 'build':
            if not data['builds']:
                raise ValueError('No builds configured')
            for build in data['builds']:
                output = Path(build['output'])
                command = ['colcon', '--log-base', str(output / 'log'), 'build',
                           '--base-paths', *build['sources'], '--build-base', str(output / 'build'),
                           '--install-base', str(output / 'install'), *build['colcon_args']]
                result = supervise(dict(setup=build['setup'], env={}, processes=[
                    dict(name='colcon', command=command)]))
                if result:
                    return result
            return 0
        if args.action == 'deploy':
            if not args.output or not args.image or args.partition < 1:
                raise ValueError('deploy requires --output STAGED_ROOT --image IMAGE and positive partition')
            installer = prefix() / 'lib/chimaera_ros_bridge/deploy_image.sh'
            return subprocess.call(['bash', str(installer), args.image, args.output, str(args.partition)])
        if args.action == 'stage':
            if not args.output:
                raise ValueError('stage requires --output')
            print(stage(data, args.output))
            return 0
        if args.action == 'bringup' and args.side != 'host':
            raise ValueError('bringup runs on the host')
        spec = plan(data, args.side, package_prefix='/unused' if args.guest_binary else None,
                    simulate=args.action == 'bringup')
        if args.guest_binary:
            spec['processes'][0]['command'][0] = args.guest_binary
        if args.action == 'plan':
            print(json.dumps(spec, indent=2))
            return 0
        return supervise(spec)
    except (ValueError, OSError) as error:
        print('chimaera_ros: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
