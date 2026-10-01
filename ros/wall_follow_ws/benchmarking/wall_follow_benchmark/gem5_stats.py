"""Summarize the last complete gem5 statistics dump (cumulative since workbegin)."""
import math
from pathlib import Path
import re

METRICS = ('sim_seconds', 'instructions', 'cycles', 'ipc', 'l1d_misses',
           'l1i_misses', 'l2_misses', 'l1i_mpki', 'l1d_mpki', 'l2_mpki')


def summarize(path):
    current, last = None, None
    for line in Path(path).read_text().splitlines():
        if 'Begin Simulation Statistics' in line:
            current = {}
        elif 'End Simulation Statistics' in line and current is not None:
            last, current = current, None
        elif current is not None:
            fields = line.split()
            if len(fields) >= 2:
                try:
                    value = float(fields[1])
                except ValueError:
                    continue
                if math.isfinite(value):
                    current[fields[0]] = value
    if last is None:
        raise ValueError('No complete gem5 statistics dump')
    def total(pattern):
        values = [v for k, v in last.items() if re.fullmatch(pattern, k)]
        return sum(values) if values else None
    # SwitchableProcessor names its ROI cores "switch". Never include KVM boot
    # cores in cycle/IPC totals, even when their stats appear in the same dump.
    cycles = total(r'board\.processor\.switch\d+\.core\.numCycles')
    instructions = last.get('simInsts')
    result = dict(sim_seconds=last.get('simSeconds'), instructions=instructions,
                cycles=cycles, ipc=instructions / cycles if cycles else None,
                l1d_misses=total(r'board\.cache_hierarchy\.ruby_system\.l1_controllers\d+\.L1Dcache\.m_demand_misses'),
                l1i_misses=total(r'board\.cache_hierarchy\.ruby_system\.l1_controllers\d+\.L1Icache\.m_demand_misses'),
                l2_misses=total(r'board\.cache_hierarchy\.ruby_system\.l2_controllers\d*\.L2cache\.m_demand_misses'))
    for level in ('l1i', 'l1d', 'l2'):
        misses = result[f'{level}_misses']
        result[f'{level}_mpki'] = 1000 * misses / instructions if instructions and misses is not None else None
    return result


def load_metrics(path):
    path = Path(path)
    if not (path / 'gem5/stats.txt').is_file():
        return {f'gem5_{key}': None for key in METRICS}
    return {f'gem5_{key}': value for key, value in summarize(path / 'gem5/stats.txt').items()}
