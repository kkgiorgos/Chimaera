#!/usr/bin/env python3
"""Monitor saved host records without contacting the running simulation."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import sys
import time


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_csv(path):
    try:
        lines = path.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        return []
    # Writers flush one row per exchange; ignore a row still being written.
    if lines and not lines[-1].endswith('\n'):
        lines.pop()
    return [row for row in csv.DictReader(lines) if None not in row and None not in row.values()]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def guest_failure(path):
    try:
        serial = path.read_text(errors='replace')
    except FileNotFoundError:
        return None
    for pattern in (r'^Guest bridge: .+$', r'^\w+ exited \(-?\d+\); stopping session\r?$',
                    r'^CHIMAERA_GUEST_APPLICATION_EXIT=\d+\r?$'):
        match = re.search(pattern, serial, re.MULTILINE)
        if match:
            return match.group().strip()
    return None


def snapshot(path, case='case-003'):
    path = Path(path)
    direct = (path / 'execution.json').exists() or (path / 'timing.csv').exists()
    if direct and '-attempt-' in path.name:
        case = path.name.split('-attempt-')[0]
    suite = path.parent if direct else path
    manifest = read_json(suite / 'experiment_manifest.json')
    point = next((point for point in manifest.get('points', []) if point['id'] == case), {})
    attempts = sorted(suite.glob(case + '-attempt-*'))
    attempt = path if direct else attempts[-1] if attempts else None
    state = dict(case=case, status=point.get('status', 'pending'),
                 cpu_type=manifest.get('configuration', {}).get('hardware', {}).get('cpu_type', 'unknown'),
                 phase='Waiting for a trial directory', attempt=str(attempt) if attempt else None)
    if not attempt:
        return state
    cpu = read_json(attempt / 'gem5/cpu_roi.json')
    if cpu:
        state['cpu_type'] = cpu['cpu_type']
    timing = read_csv(attempt / 'timing.csv')
    state['phase'] = 'Waiting for robot readiness / warmup' if timing else (
        'Initializing detailed simulation' if cpu else 'Booting / staging guest')
    if timing:
        simulated = sum(number(row.get('sim_seconds')) or 0 for row in timing)
        wall = number(timing[-1].get('elapsed_wall_seconds'))
        state.update(simulated_seconds=simulated, completed_exchanges=len(timing),
                     wall_seconds=wall, average_speed=simulated / wall if wall and wall > 0 else None,
                     last_update_age_seconds=max(0, time.time() - (attempt / 'timing.csv').stat().st_mtime))
    commands = read_csv(attempt / 'commands.csv')
    state['delivered_commands'] = len(commands)
    if commands:
        receipt, capture = (number(commands[-1].get(key)) for key in ('receipt_time', 'command_time'))
        if receipt is not None and capture is not None:
            state['last_command_age_ms'] = (receipt - capture) * 1000
    launch = read_json(attempt / 'launch.json')
    result = read_json(attempt / 'result.json')
    if launch and number(launch.get('launch_time')) is not None:
        state['phase'] = 'Ball launched'
        duration = number(read_json(attempt / 'execution.json').get('fixed', {}).get('duration'))
        elapsed = max(0, state.get('simulated_seconds', 0) - launch['launch_time'])
        state.update(throw_seconds=elapsed, duration_seconds=duration,
                     throw_percent=min(100, 100 * elapsed / duration) if duration and duration > 0 else None)
    if type(result.get('success')) is bool:
        state.update(phase='Task scored', outcome='CATCH' if result['success'] else 'MISS')
    if point.get('reason'):
        state['reason'] = point['reason']
    failure = guest_failure(attempt / 'gem5/board.pc.com_1.device')
    if failure and 'outcome' not in state:
        state.update(status='infrastructure_failure', phase='Guest application failed',
                     reason=state.get('reason', failure),
                     host_running_after_guest_failure=point.get('status') == 'running')
    return state


def display(state):
    lines = [f"{state['cpu_type'].upper()} · {state['case']} · {state['status']}", state['phase']]
    if state.get('attempt'):
        lines.append(state['attempt'])
    if 'simulated_seconds' in state:
        wall = state.get('wall_seconds')
        speed = state.get('average_speed')
        lines.append(f"Simulated: {state['simulated_seconds']:.6f} s | completed exchanges: {state['completed_exchanges']}")
        if wall is not None and speed is not None:
            lines.append(f"Wall time at last exchange: {wall / 60:.1f} min | average speed: {speed:.6f}×")
        lines.append(f"Timing file last updated {state['last_update_age_seconds']:.1f} wall seconds ago")
    if 'delivered_commands' in state:
        age = state.get('last_command_age_ms')
        lines.append(f"Commands delivered: {state['delivered_commands']}"
                     + (f" | latest command age on arrival: {age:.1f} ms" if age is not None else ''))
    if state.get('throw_percent') is not None:
        lines.append(f"Throw: {state['throw_seconds']:.3f} / {state['duration_seconds']:.3f} simulated seconds"
                     + f" ({state['throw_percent']:.1f}%)")
    if state.get('outcome'):
        lines.append('Result: ' + state['outcome'])
    if state.get('reason'):
        lines.append('Reason: ' + state['reason'])
    if state.get('host_running_after_guest_failure'):
        lines.append('Host runner still reports running; guest work has failed.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Suite directory or an individual attempt')
    parser.add_argument('--case', default='case-003')
    parser.add_argument('--interval', type=float, default=5, help='Wall seconds between updates')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.output.is_dir():
        parser.error('Output directory does not exist')
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error('--interval must be finite and positive')
    try:
        while True:
            state = snapshot(args.output, args.case)
            if sys.stdout.isatty() and not args.once:
                print('\033[2J\033[H', end='')
            print(display(state), flush=True)
            if args.once or state['status'] in ('complete', 'interrupted', 'infrastructure_failure'):
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
