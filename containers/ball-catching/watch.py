#!/usr/bin/env python3
"""Monitor all container workers from their saved host records; no ROS required."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ros/ball_catching_ws/scripts'))
from watch_memory_suite import snapshot


def states(output):
    try:
        run = json.loads((output / 'run.json').read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [dict(worker_status=record['status'], **snapshot(output / 'jobs' / case / 'suite', case))
            for case, record in run['jobs'].items()]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path)
    cli.add_argument('--once', action='store_true')
    cli.add_argument('--interval', type=float, default=10)
    args = cli.parse_args()
    if args.interval <= 0:
        cli.error('interval must be positive')
    try:
        while True:
            rows = states(args.output)
            if not rows:
                print('Waiting for container run.json', flush=True)
            for row in rows:
                sim = row.get('simulated_seconds')
                print(f'{row["case"]}: {row["worker_status"]} · {row["phase"]}' +
                      (f' · {sim:.3f} simulated s' if sim is not None else ''), flush=True)
            if args.once:
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
