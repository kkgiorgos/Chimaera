#!/usr/bin/env python3
"""Report exactly the collected run paths using Chimaera's current dashboard API."""

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ros/wall_follow_ws/scripts'))
from compare_experiments import discover
from build_dashboard import build_dashboard
from wall_follow_benchmark.comparison import write_summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--warmup', type=float, default=0.)
    args = parser.parse_args()
    if not math.isfinite(args.warmup) or args.warmup < 0:
        parser.error('warmup must be finite and nonnegative')
    try:
        paths = json.loads(args.runs_file.read_text())
        if not isinstance(paths, list) or not paths or any(type(p) is not str for p in paths):
            raise ValueError('runs file must be a nonempty JSON array of paths')
        runs = discover(list(map(Path, paths)))
        if not runs:
            raise ValueError('No eligible runs found')
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'included_runs.json').write_text(json.dumps(list(map(str, runs)), indent=2))
        payload = build_dashboard(runs, args.output / 'dashboard.html', warmup=args.warmup)
        write_summaries(payload['runs'], args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
