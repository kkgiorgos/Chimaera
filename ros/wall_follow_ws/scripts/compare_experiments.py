#!/usr/bin/env python3
"""Discover completed runs in one or more copied suites and generate a dashboard and summaries."""
import argparse
import json
import math
from pathlib import Path
import sys

from run_experiments import eligible


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
    parser.add_argument('--runs-file', type=Path, help='JSON array of exact run paths, instead of roots')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--warmup', type=float, default=0.)
    parser.add_argument('--max-points', type=int, default=1500, help='Maximum points per aggregate trace')
    parser.add_argument('--include-incomplete', action='store_true', help='Include partial attempts for diagnosis')
    args = parser.parse_args()
    if not math.isfinite(args.warmup) or args.warmup < 0 or args.max_points < 2:
        parser.error('warmup must be finite and nonnegative; max-points must be at least 2')
    try:
        if args.runs_file:
            if args.roots:
                raise ValueError('use roots or --runs-file, not both')
            paths = json.loads(args.runs_file.read_text())
            if not isinstance(paths, list) or not paths or any(type(p) is not str for p in paths):
                raise ValueError('runs file must be a nonempty JSON array of paths')
            args.roots = list(map(Path, paths))
        if not args.roots:
            raise ValueError('provide roots or --runs-file')
        runs = discover([r.expanduser() for r in args.roots], args.include_incomplete)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not runs:
        parser.error('No eligible runs found')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'included_runs.json').write_text(json.dumps([str(r) for r in runs], indent=2))
    from build_dashboard import build_dashboard
    from wall_follow_benchmark.comparison import write_summaries
    payload = build_dashboard(runs, args.output/'dashboard.html', warmup=args.warmup, max_points=args.max_points)
    write_summaries(payload['runs'], args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
