#!/usr/bin/env python3
"""Collect isolated worker results into one offline comparison dashboard."""
import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ros/ball_catching_ws/scripts'))
from report import create_dashboard
from run_memory_suite import write_json


def collect(inputs, output):
    points = copy.deepcopy(json.loads((inputs / 'plan.json').read_text())['points'])
    directories = []
    for point in points:
        suite = output / 'jobs' / point['id'] / 'suite'
        path = suite / 'experiment_manifest.json'
        if not path.exists():
            continue
        source = next(p for p in json.loads(path.read_text())['points'] if p['id'] == point['id'])
        point.update(status=source['status'], trials=source['trials'])
        if source.get('reason'):
            point['reason'] = source['reason']
        if source.get('config'):
            point['config'] = source['config']
        for trial in source['trials']:
            directories.append(suite / trial)
    destination = output / 'comparison'
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'points.json', points)
    create_dashboard(directories, destination / 'dashboard.html', points)
    return destination / 'dashboard.html'


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--inputs', type=Path, default=Path('/assets/inputs'))
    cli.add_argument('--output', type=Path, default=Path('/output'))
    args = cli.parse_args()
    try:
        collect(args.inputs, args.output)
    except (OSError, ValueError, KeyError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    main()
