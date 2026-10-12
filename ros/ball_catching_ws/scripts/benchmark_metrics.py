"""Read run-level guest DRAM measurements without averaging percentiles."""
import csv
import math
from pathlib import Path
import re

import yaml


def read_stats(path):
    text = Path(path).read_text()
    blocks = text.split('---------- Begin Simulation Statistics ----------')
    result = {}
    for line in blocks[-1].splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                value = float(fields[1])
            except ValueError:
                continue
            if math.isfinite(value):
                result[fields[0]] = value
    return result


def percentile(histogram, fraction):
    total = sum(histogram.values())
    if not total:
        return None
    target = math.ceil(fraction * total)
    cumulative = 0
    for value, count in sorted(histogram.items()):
        cumulative += count
        if cumulative >= target:
            return value


def controller_counters(path):
    """Only physically served commands contribute to DRAM bus bandwidth."""
    result = dict(read=0, write=0)
    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            for kind in result:
                result[kind] += value.get('num_' + kind + '_reqs_served', 0)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
    visit(yaml.safe_load(Path(path).read_text()))
    return result


def memory_metrics(directory, peak_gbps):
    directory = Path(directory)
    stats = read_stats(directory / 'stats.txt')
    prefixes = {key.rsplit('.', 1)[0] for key in stats if key.endswith('.cpuReads')}
    if len(prefixes) != 1:
        raise ValueError('Expected one Ramulator wrapper with guest DRAM statistics')
    prefix = prefixes.pop()
    get = lambda name: stats.get(prefix + '.' + name, 0)
    frequency = stats.get('simFreq', 1e12)
    if frequency <= 0:
        raise ValueError('Missing or empty memory measurement window')
    seconds = stats['simTicks'] / frequency if 'simTicks' in stats else stats.get('simSeconds', 0)
    if seconds <= 0:
        raise ValueError('Missing or empty memory measurement window')
    samples = get('cpuReads')
    result = dict(samples=int(samples), measurement_seconds=seconds,
                  guest_retries=int(get('cpuRetries')),
                  aggressor_accepted=int(get('aggressorAccepted')),
                  aggressor_blocked_attempts=int(get('aggressorRejected')),
                  aggressor_stall_cycles=int(get('aggressorStallCycles')),
                  aggressor_accepted_gbps=get('aggressorBytes') / seconds / 1e9,
                  latency_resolution_ns=1,
                  latency_end='Ramulator backend read completion',
                  guest_scope='all guest DRAM reads; instruction, data, OS and ROS clients')
    for metric, histogram_name, sum_name in [
            ('accepted', 'cpuReadLatencyNs', 'cpuReadLatencyTicks'),
            ('total', 'cpuReadTotalLatencyNs', 'cpuReadTotalLatencyTicks')]:
        histogram = {}
        pattern = re.compile(re.escape(prefix + '.' + histogram_name + '::') + r'(\d+)$')
        for key, value in stats.items():
            match = pattern.fullmatch(key)
            if match:
                histogram[int(match[1])] = value
        if sum(histogram.values()) != samples:
            raise ValueError(f'{histogram_name} sample count mismatch; rebuild gem5')
        result[metric + '_mean_ns'] = get(sum_name) / samples / frequency * 1e9 if samples else None
        result[metric + '_p95_ns'] = percentile(histogram, .95)
        result[metric + '_p99_ns'] = percentile(histogram, .99)
        result[metric + '_histogram_ns'] = [[key, int(value)] for key, value in sorted(histogram.items())]
    snapshots = list(directory.glob('*.ramulator_stats.yaml'))
    if len(snapshots) != 1:
        raise ValueError('Expected final Ramulator counters')
    counters = controller_counters(snapshots[0])
    bus = sum(counters.values()) * 64 / seconds / 1e9
    result.update(dram_bus_gbps=bus, dram_bus_peak_fraction=bus / peak_gbps,
                  dram_read_commands=counters['read'], dram_write_commands=counters['write'])
    return result


def command_metrics(path, start, end, control_hz):
    with Path(path).open() as source:
        records = [{key: float(value) for key, value in row.items()}
                   for row in csv.DictReader(source)]
    times = sorted(set(row['receipt_time'] for row in records
                       if start <= row['receipt_time'] <= end))
    gaps = [b - a for a, b in zip(times, times[1:])]
    return dict(delivered_commands=len(times),
                delivered_control_hz=(len(times) - 1) / (times[-1] - times[0])
                if len(times) > 1 else None,
                command_gap_p95_ms=sorted(gaps)[math.ceil(.95 * len(gaps)) - 1] * 1000
                if gaps else None,
                command_late_fraction=sum(gap > 1.5 / control_hz for gap in gaps) / len(gaps)
                if gaps else None)
