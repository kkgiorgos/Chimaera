#!/usr/bin/env python3
"""Select global run IDs while delegating execution to Chimaera's current runner."""

import argparse
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / 'ros/wall_follow_ws/scripts'
if SCRIPTS.is_dir():
    sys.path.insert(0, str(SCRIPTS))
import run_experiments

make_plan = run_experiments.make_plan


def select_runs(plan, run_ids):
    if (not isinstance(run_ids, list) or not run_ids
            or any(type(value) is not str for value in run_ids)
            or len(set(run_ids)) != len(run_ids)):
        raise ValueError('run IDs must be a nonempty JSON array of distinct strings')
    ids = set(run_ids)
    selected = [run for run in plan['runs'] if run['id'] in ids]
    if len(selected) != len(run_ids):
        raise ValueError('selection contains unknown run IDs')
    if [run['id'] for run in selected] != run_ids:
        raise ValueError('selected run IDs must follow the original plan order')
    return selected


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--run-ids-file', type=Path)
    args, remaining = parser.parse_known_args()
    original = run_experiments.make_plan
    if args.run_ids_file:
        try:
            ids = json.loads(args.run_ids_file.read_text())
            def selected_plan(*arguments, **keywords):
                plan = original(*arguments, **keywords)
                return dict(plan, runs=select_runs(plan, ids), selected_run_ids=ids)
            run_experiments.make_plan = selected_plan
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    argv = sys.argv
    try:
        sys.argv = [argv[0], *remaining]
        return run_experiments.main()
    finally:
        run_experiments.make_plan = original
        sys.argv = argv


if __name__ == '__main__':
    raise SystemExit(main())
