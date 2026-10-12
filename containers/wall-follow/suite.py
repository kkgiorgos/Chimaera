#!/usr/bin/env python3
"""Prepare, execute, and report a complete container benchmark suite."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

from workflow import DIRECTORY, prepare, report_command, run_script
from configure import make_plan


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, default=DIRECTORY.parents[1] / 'ros/wall_follow_ws/experiments/demo.json')
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--local', action='store_true', help='Native controller; skip guest preparation')
    cli.add_argument('--workers', type=int, default=1)
    cli.add_argument('--jobs', type=int, default=3, help='Build parallelism')
    cli.add_argument('--image', default='chimaera:jammy-humble-fortress')
    cli.add_argument('--architecture', default=None)
    cli.add_argument('--warmup', type=float, default=0)
    cli.add_argument('--resume', action='store_true')
    cli.add_argument('--rebuild-image', action='store_true')
    args = cli.parse_args()
    output = args.output.expanduser().resolve()
    inputs = output.with_name(output.name + '-inputs')
    architecture = args.architecture or ('local' if args.local else 'gem5')
    try:
        if args.workers < 1 or args.jobs < 1:
            raise ValueError('workers and jobs must be positive')
        if not math.isfinite(args.warmup) or args.warmup < 0:
            raise ValueError('warmup must be finite and nonnegative')
        make_plan(json.loads(args.config.read_text()), architecture, gem5=not args.local)
        if args.resume:
            if args.rebuild_image:
                raise ValueError('resume preserves the original image; use a fresh output to rebuild')
            inputs = inputs / 'plan'
            config = json.loads((inputs / 'runner.json').read_text())
            command = config['command']
            original = inputs / 'source-experiment.json'
            if not original.exists():
                original = inputs / 'experiment.json'
            if (json.loads(original.read_text()) != json.loads(args.config.read_text())
                    or config['image'] != args.image
                    or ('--gem5' in command) == args.local
                    or command[command.index('--architecture') + 1] != architecture):
                raise ValueError('resume requires the original experiment, image, mode, and architecture')
        else:
            if output.exists() or inputs.exists():
                raise ValueError('output or sibling inputs already exist; use --resume or a fresh output')
            # Inputs are outside the result tree, as required by the shared runner.
            inputs.mkdir(parents=True)
            overlay = inputs / 'overlay'
            prepare(args.image, overlay, args.jobs, args.rebuild_image)
            if not args.local:
                run_script(DIRECTORY / 'prepare-guest.py', '--sdk-image', args.image, '--overlay', overlay)
            # configure.py owns its output directory; keep it next to the overlay.
            run_script(DIRECTORY / 'configure.py', '--config', args.config.resolve(),
                       '--output', inputs / 'plan', '--image', args.image, '--overlay', overlay,
                       '--architecture', architecture, *(['--local'] if args.local else []))
            inputs = inputs / 'plan'
        runner_config = inputs / 'runner.json'
        code = subprocess.call([sys.executable, str(DIRECTORY.parent / 'run.py'),
                                '--config', str(runner_config), '--output', str(output),
                                '--workers', str(args.workers), *(['--resume'] if args.resume else [])])
        report = subprocess.call(report_command(args.image, output, args.warmup))
        if report == 0:
            print(f'Report: {output / "comparison/dashboard.html"}', flush=True)
        return code or report
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
