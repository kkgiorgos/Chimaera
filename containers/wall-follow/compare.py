#!/usr/bin/env python3
"""Compare completed wall-follow jobs from a generic container run."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / 'ros/wall_follow_ws/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / 'benchmarking'))
from wall_follow_benchmark.results import eligible


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True, help='Container runner output')
    cli.add_argument('--report', type=Path, required=True, help='Comparison destination')
    cli.add_argument('--warmup', type=float, default=0)
    args = cli.parse_args()
    try:
        root = args.output.resolve(strict=True)
        state = json.loads((root / 'run.json').read_text())
        if state['status'] == 'running':
            raise ValueError('wait for the container run to finish before comparing')
        selected = []
        for name, record in state['jobs'].items():
            if record['status'] == 'completed':
                attempts = sorted((root / 'jobs' / name / 'suite/runs' / name).glob('attempt_*'))
                valid = [path for path in attempts if eligible(path)]
                if valid:
                    selected.append(str(valid[-1]))
        if not selected:
            raise ValueError('no completed wall-follow results')
        # The comparison script marks coverage against the supplied list only.
        print(f'Comparing {len(selected)}/{len(state["jobs"])} planned jobs', flush=True)
        selection = root / 'comparison-runs.json'
        selection.write_text(json.dumps(selected, indent=2) + '\n')
        code = subprocess.call([sys.executable, str(SCRIPTS / 'compare_experiments.py'),
                                '--runs-file', str(selection), '--output', str(args.report),
                                '--warmup', str(args.warmup)])
        if code == 0:
            complete = len(selected) == len(state['jobs'])
            coverage = dict(status='completed' if complete else 'partial',
                            planned=list(state['jobs']), selected=selected)
            (args.report / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
            if not complete:
                dashboard = args.report / 'dashboard.html'
                notice = (f'<aside>Partial comparison: {len(selected)}/{len(state["jobs"])} '
                          'planned jobs. See coverage.json and the run.json job statuses.</aside>')
                dashboard.write_text(dashboard.read_text().replace('<body>', '<body>' + notice, 1))
        return code
    except (OSError, ValueError, KeyError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
