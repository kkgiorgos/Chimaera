#!/usr/bin/env python3
"""Run a prepared suite with configurable container concurrency and reporting."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

DIRECTORY = Path(__file__).resolve().parent


def report_command(inputs, output):
    config = json.loads((inputs / 'runner.json').read_text())
    return ['docker', 'run', '--rm', '--init', '--network', 'none', '--read-only',
        '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777',
        '--mount', f'type=bind,source={inputs / "app"},target=/app,readonly',
        '--mount', f'type=bind,source={inputs},target=/assets/inputs,readonly',
        '--mount', f'type=bind,source={output},target=/output', config['image'],
        'python3', '/app/containers/ball-catching/compare.py']


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--inputs', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--workers', type=int, default=1)
    cli.add_argument('--resume', action='store_true')
    cli.add_argument('--dry-run', action='store_true')
    cli.add_argument('--report-only', action='store_true')
    args = cli.parse_args()
    inputs, output = args.inputs.resolve(), args.output.resolve()
    try:
        if args.report_only:
            return subprocess.call(report_command(inputs, output))
        command = [sys.executable, str(DIRECTORY.parent / 'run.py'), '--config', str(inputs / 'runner.json'),
                   '--output', str(output), '--workers', str(args.workers),
                   *(['--resume'] if args.resume else []), *(['--dry-run'] if args.dry_run else [])]
        code = subprocess.call(command)
        if not args.dry_run and output.exists():
            # Generate the report for failed/partial suites too.
            report_code = subprocess.call(report_command(inputs, output))
            code = code or report_code
            print(f'Dashboard: {output / "comparison/dashboard.html"}', flush=True)
        return code
    except (OSError, ValueError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
