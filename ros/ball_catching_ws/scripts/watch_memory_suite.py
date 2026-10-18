#!/usr/bin/env python3
"""Monitor saved host records without contacting the running simulation."""
import argparse
import csv
import json
import math
import re
from pathlib import Path
import sys
import time

from benchmark_status import guest_failure, host_failure, launch_failure


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


def image_stages(path):
    try:
        content = path.read_text(errors='replace')
    except FileNotFoundError:
        return {}
    stages = {}
    pattern = (r'CHIMAERA_IMAGE component=(bridge|perception) stage=(\w+) '
               r'capture_ns=(-?\d+) now_ns=(-?\d+) age_ns=(-?\d+) budget_ns=(\d+)\r?\n')
    for component, stage, capture, now, age, budget in re.findall(pattern, content):
        capture, now, age, budget = map(int, (capture, now, age, budget))
        if now - capture != age:
            continue
        stages[component + '.' + stage] = dict(capture_ns=capture, now_ns=now,
            age_ms=age / 1e6, budget_ms=budget / 1e6)
    return stages


def snapshot(path, case=None):
    path = Path(path)
    direct = (path / 'execution.json').exists() or (path / 'timing.csv').exists()
    if direct and '-attempt-' in path.name:
        case = path.name.split('-attempt-')[0]
    suite = path.parent if direct else path
    manifest = read_json(suite / 'experiment_manifest.json')
    if case is None:
        points = manifest.get('points', [])
        active = next((p['id'] for p in points if p.get('status') == 'running'), None)
        prior = list(suite.glob('case-*-attempt-*'))
        latest = max(prior, key=lambda p: p.stat().st_mtime) if prior else None
        case = (active or (latest.name.split('-attempt-')[0] if latest else None)
                or (points[0]['id'] if points else 'case-003'))
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
    readiness = read_csv(attempt / 'readiness.csv')
    if readiness:
        row = readiness[-1]
        if row.get('arm_ready') in ('0', '1') and row.get('perception_ready') in ('0', '1'):
            state['readiness'] = dict(arm=row['arm_ready'] == '1',
                perception=row['perception_ready'] == '1',
                warmup_elapsed=number(row.get('warmup_elapsed')),
                warmup_required=number(row.get('warmup_required')))
            if timing:
                ready = state['readiness']
                if ready['arm'] and ready['perception']:
                    remaining = (ready['warmup_required'] or 0) - (ready['warmup_elapsed'] or 0)
                    state['phase'] = ('Extra warmup after readiness' if remaining > 0
                                      else 'Ready; waiting for launch')
                elif ready['arm']:
                    state['phase'] = 'Waiting for fresh camera readiness'
                elif ready['perception']:
                    state['phase'] = 'Waiting for arm readiness'
                else:
                    state['phase'] = 'Waiting for arm and camera readiness'
    stages = image_stages(attempt / 'gem5/board.pc.com_1.device')
    if stages:
        state['image_stages'] = stages
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
    launch_error = launch_failure(attempt / 'launch_validation.json')
    if type(result.get('success')) is bool and not launch_error:
        state.update(phase='Task scored', outcome='CATCH' if result['success'] else 'MISS')
    if point.get('reason'):
        state['reason'] = point['reason']
    guest_error = guest_failure(attempt / 'gem5/board.pc.com_1.device')
    failure = guest_error or host_failure(attempt / 'launch.log')
    if failure and 'outcome' not in state:
        phase = 'Guest application failed' if guest_error else 'Co-simulation component failed'
        reason = state.get('reason', failure) if guest_error else failure
        state.update(status='infrastructure_failure', phase=phase, reason=reason,
                     host_running_after_guest_failure=point.get('status') == 'running')
    if launch_error:
        state.update(status='infrastructure_failure', phase='Ball launch failed', reason=launch_error)
    return state


def preparation_snapshot(path):
    """Follow preparation and checkpoint records without touching live sockets."""
    path = Path(path)
    state = read_json(path / 'preparation.json')
    progress = read_json(path / 'checkpoints/progress.json')
    if state.get('phase') == 'Creating readiness checkpoints' or not state:
        identifier = progress.get('checkpoint')
        if identifier and re.fullmatch(r'[0-9a-f]{64}', identifier):
            attempt = path / 'checkpoints' / identifier
        else:
            # Older preparations have no progress.json; retain their diagnostics.
            attempts = list((path / 'checkpoints').glob('*/execution.json'))
            attempt = max(attempts, key=lambda p: p.stat().st_mtime).parent if attempts else None
        if attempt:
            state = snapshot(attempt)
            state.update(read_json(attempt / 'checkpoint-stage.json'))
            if (attempt / 'checkpoint.json').is_file():
                state['phase'] = 'Readiness checkpoint saved'
            elif (attempt / 'checkpoint-ready').is_file() and not (attempt / 'checkpoint-stage.json').exists():
                state['phase'] = 'Waiting for guest checkpoint barrier'
            elif state['phase'] == 'Ready; waiting for launch':
                state['phase'] = 'Ready; waiting for checkpoint barrier'
            if progress:
                state.update(checkpoint_current=progress.get('current'), checkpoint_total=progress.get('total'))
            if progress.get('status') == 'failed' or state.get('status') == 'infrastructure_failure':
                state.update(phase='Readiness checkpoint failed',
                             reason=progress.get('error') or state.get('reason'))
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
    if 'readiness' in state and 'throw_percent' not in state and 'outcome' not in state:
        ready = state['readiness']
        lines.append('Arm ready: ' + ('yes' if ready['arm'] else 'no')
                     + ' | fresh camera ready: ' + ('yes' if ready['perception'] else 'no'))
        if ready['warmup_elapsed'] is not None and ready['warmup_required'] is not None:
            if ready['arm'] and ready['perception']:
                lines.append(f"Extra warmup: {ready['warmup_elapsed']:.3f} / {ready['warmup_required']:.3f} simulated seconds")
            elif ready['warmup_required'] > 0:
                lines.append(f"Extra warmup starts after readiness: {ready['warmup_required']:.3f} simulated seconds configured")
            else:
                lines.append('No extra warmup configured; launch waits for readiness')
    for component in ('bridge', 'perception'):
        stages = [(key.split('.', 1)[1], value) for key, value in state.get('image_stages', {}).items()
                  if key.startswith(component + '.')]
        if stages:
            lines.append(component.capitalize() + ' image age (latest sample per stage): '
                         + ', '.join(f"{stage}={value['age_ms']:.1f} ms" for stage, value in stages))
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
    parser.add_argument('--case', help='Inspect a specific case; by default follow the active or latest attempt')
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
