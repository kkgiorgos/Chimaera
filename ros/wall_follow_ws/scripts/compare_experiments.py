#!/usr/bin/env python3
"""Discover completed runs in one or more copied suites and generate comparison plots."""
import argparse
import json
import math
from pathlib import Path
import sys

from run_experiments import WORKSPACE, eligible


def discover(roots, include_incomplete=False):
    found = set()
    for root in roots:
        if not root.is_dir():
            raise ValueError(f'Not a directory: {root}')
        candidates = [root/'metadata.json'] if (root/'metadata.json').exists() else root.rglob('metadata.json')
        for metadata in candidates:
            run = metadata.parent.resolve()
            if eligible(run) or (include_incomplete and (run/'samples.csv').is_file()):
                found.add(run)
            else:
                print(f'Skipping incomplete run: {run}', file=sys.stderr)
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='*', type=Path, help='Suite directories or individual run directories')
    parser.add_argument('--runs-file', type=Path, help='JSON array of run directories, for large comparisons')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--warmup', type=float, default=5.)
    parser.add_argument('--include-incomplete', action='store_true', help='Also plot partial runs for diagnosis')
    args = parser.parse_args()
    if not math.isfinite(args.warmup) or args.warmup < 0:
        parser.error('warmup must be finite and nonnegative')
    try:
        roots = args.roots
        if args.runs_file:
            listed = json.loads(args.runs_file.read_text())
            if not isinstance(listed, list) or not listed or any(type(p) is not str for p in listed):
                raise ValueError('runs file must be a nonempty JSON array of paths')
            roots = [*roots, *map(Path, listed)]
        if not roots:
            raise ValueError('provide run directories or --runs-file')
        runs = discover([r.expanduser() for r in roots], args.include_incomplete)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not runs:
        parser.error('No eligible runs found')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'included_runs.json').write_text(json.dumps([str(r) for r in runs], indent=2))
    # Source-local plotting also works on copied results without a sourced ROS installation.
    sys.path.insert(0, str(WORKSPACE/'benchmarking'))
    from wall_follow_benchmark.plot import main as plot
    sys.argv = ['plot', *map(str, runs), '--output', str(args.output), '--warmup', str(args.warmup)]
    plot()
    from build_dashboard import build_dashboard
    build_dashboard(runs, args.output/'dashboard.html', warmup=args.warmup)
    return 0


if __name__ == '__main__':
    sys.exit(main())
