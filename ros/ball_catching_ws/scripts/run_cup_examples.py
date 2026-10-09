#!/usr/bin/env python3
"""Run cup demonstrations from upright home and build one offline dashboard."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from report import create_dashboard


FLAT_CASES = {
    'default': ('Default · 3 m/s', []),
    'diagonal': ('Diagonal · 3 m/s', ['--position', '2.08', '-0.11', '2',
                                   '--direction', '-1', '0.1', '0']),
    'fast': ('Faster flight · 5 m/s', ['--position', '3.05', '0.04', '2', '--speed', '5']),
    'inclined': ('Inclined throw · 3.2 m/s', ['--position', '2.18', '0.10', '2',
                                          '--direction', '-1', '0', '0.05', '--speed', '3.2']),
    'miss': ('Intentional miss · 10 m/s', ['--speed', '10']),
}
CASES = {
    'default': ('Default loft · 3 m/s', ['--position', '1.8', '0.04', '3',
                                      '--direction', '-1', '0', '2']),
    'diagonal': ('Diagonal loft · 3 m/s', ['--position', '1.8', '-0.11', '3',
                                        '--direction', '-1', '0.12', '2']),
    'fast': ('Faster loft · 5 m/s', ['--position', '4.36', '0.04', '3',
                                  '--direction', '-1', '0', '1', '--speed', '5']),
    'inclined': ('Inclined loft · 3.2 m/s', ['--position', '2.0', '0.10', '3',
                                           '--direction', '-1', '0', '2', '--speed', '3.2']),
    'miss': FLAT_CASES['miss'],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', choices=list(CASES), nargs='+', default=list(CASES))
    parser.add_argument('--wall-timeout', type=float, default=120.)
    parser.add_argument('--flat', action='store_true', help='Use the earlier, shorter flat throws as challenges')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output directory must be empty or new')
    if len(set(args.cases)) != len(args.cases):
        parser.error('each case may be selected only once')
    output.mkdir(parents=True, exist_ok=True)
    cases = FLAT_CASES if args.flat else CASES
    cameras = [] if args.flat else ['--camera-position', '-2', '0', '2', '--resolution', '1280', '960']
    points = [dict(id=name, label=cases[name][0], trials=[], expected_throws=1,
                   status='pending', config={}) for name in args.cases]
    manifest = dict(mode='cup', start_condition='vertical_home', points=points)
    completed = []

    def report():
        (output / 'experiment_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        create_dashboard(completed, output / 'dashboard.html', points)

    report()
    for name, point in zip(args.cases, points):
        print(f'Running {point["label"]}', flush=True)
        trial = output / name
        subprocess.run([sys.executable, str(Path(__file__).with_name('run_trial.py')),
                        '--mode', 'cup', '--output', str(trial), '--no-dashboard',
                        '--wall-timeout', str(args.wall_timeout), *cameras, *cases[name][1]])
        if not (trial / 'result.json').exists():
            point.update(status='infrastructure_failure', reason=f'Inspect {name}/launch.log')
            report()
            return 2
        point.update(status='complete', trials=[name],
                     config=json.loads((trial / 'experiment.json').read_text()))
        completed.append(trial)
        report()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
