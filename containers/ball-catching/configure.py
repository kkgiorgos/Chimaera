#!/usr/bin/env python3
"""Freeze portable benchmark inputs and export jobs for the shared runner."""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]
SCRIPTS = ROOT / 'ros/ball_catching_ws/scripts'
sys.path.insert(0, str(SCRIPTS))
from run_memory_suite import digest, load_config, plan, write_json


def validate_limits(cpus, memory):
    if not math.isfinite(cpus) or cpus <= 0 or not re.fullmatch(r'[1-9][0-9]*[bkmgBKMG]?', memory):
        raise ValueError('Worker CPU and memory limits must be positive Docker resource limits')


def configure(config, output, image, disk, kernel, cpus=4, memory='12g'):
    validate_limits(cpus, memory)
    data = load_config(config)
    points = plan(data)
    disk, kernel = disk.resolve(strict=True), kernel.resolve(strict=True)
    if not disk.is_file() or not kernel.is_file():
        raise ValueError('Guest disk and kernel must be files')
    output = output.resolve()
    if output.exists():
        raise ValueError('Inputs output already exists; use a fresh directory')
    output.mkdir(parents=True)
    (output / 'COLCON_IGNORE').touch()
    app = output / 'app'
    for name in ('scripts', 'dashboard'):
        shutil.copytree(SCRIPTS.parent / name, app / 'ros/ball_catching_ws' / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    worker = app / 'containers/ball-catching/worker.py'
    worker.parent.mkdir(parents=True)
    shutil.copy2(DIRECTORY / 'worker.py', worker)
    shutil.copy2(DIRECTORY / 'compare.py', worker.parent / 'compare.py')
    (output / 'source-experiment.json').write_bytes(config.read_bytes())
    (output / 'memory.json').write_bytes(Path(data['ramulator_config']).read_bytes())
    exported = {key: value for key, value in data.items() if key not in ('ramulator_sha256', 'peak_gbps')}
    exported['fixed'] = dict(data['fixed'])
    modules = []
    for key, name in (('dma_module', 'chimaera_dma.ko'), ('m5_module', 'chimaera_m5.ko')):
        if data['fixed'][key]:
            target = output / name
            shutil.copyfile(data['fixed'][key], target)
            exported['fixed'][key] = '/assets/inputs/' + name
            modules.append(target)
    exported['ramulator_config'] = '/assets/inputs/memory.json'
    write_json(output / 'experiment.json', exported)
    write_json(output / 'plan.json', dict(points=points, peak_gbps=data['peak_gbps']))
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    files = [p for p in app.rglob('*') if p.is_file()] + [output / n for n in
             ('source-experiment.json', 'experiment.json', 'memory.json', 'plan.json')]
    files += modules
    write_json(output / 'inputs.json', dict(source_revision=revision,
        source_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)),
        sha256={str(p.relative_to(output)): digest(p) for p in files},
        disk=dict(size=disk.stat().st_size, mtime_ns=disk.stat().st_mtime_ns),
        kernel_sha256=digest(kernel)))
    runner = dict(image=image,
        command=['python3', '/app/containers/ball-catching/worker.py'],
        jobs=[dict(name=p['id'], args=['--case', p['id']]) for p in points],
        mounts=[dict(source='app', target='/app'), dict(source='.', target='/assets/inputs'),
                dict(source=str(disk), target='/assets/guest.img'), dict(source=str(kernel), target='/assets/kernel')],
        devices=['/dev/kvm'], cpus=cpus, memory=memory,
        env=dict(LIBGL_ALWAYS_SOFTWARE='1', ROS_LOCALHOST_ONLY='1', IGN_IP='127.0.0.1'))
    write_json(output / 'runner.json', runner)
    return runner


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, default=ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json')
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--image', default='chimaera:ball-catching')
    cli.add_argument('--disk', type=Path, default=ROOT / 'gem5/resources/x86-ubuntu-22.04-ros-humble.img')
    cli.add_argument('--kernel', type=Path, default=ROOT / 'gem5/resources/x86-linux-kernel-5.15.180')
    cli.add_argument('--cpus', type=float, default=4)
    cli.add_argument('--memory', default='12g')
    args = cli.parse_args()
    try:
        result = configure(args.config, args.output, args.image, args.disk, args.kernel, args.cpus, args.memory)
        print(f'Prepared {len(result["jobs"])} jobs: {args.output / "runner.json"}')
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    main()
