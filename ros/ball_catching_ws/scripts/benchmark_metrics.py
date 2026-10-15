"""Read run-level guest DRAM measurements without averaging percentiles."""
import csv
from decimal import Decimal, InvalidOperation
import math
import re
from pathlib import Path

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
    import yaml
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


def latency_histogram(path, name):
    """Read every bin, including scientific labels and aliases rounded by gem5."""
    block = Path(path).read_text().split('---------- Begin Simulation Statistics ----------')[-1]
    histogram, label_resolution = {}, 1
    prefix = name + '::'
    for line in block.splitlines():
        fields = line.split()
        if len(fields) < 2 or not fields[0].startswith(prefix):
            continue
        label = fields[0][len(prefix):]
        if label == 'samples':
            continue
        try:
            coordinate, count = Decimal(label), Decimal(fields[1])
        except InvalidOperation as error:
            raise ValueError(f'Invalid latency histogram bin: {fields[0]}') from error
        if any(not value.is_finite() or value < 0 or value != value.to_integral_value()
               for value in (coordinate, count)):
            raise ValueError(f'Invalid latency histogram bin: {fields[0]}')
        key = int(coordinate)
        # SparseHistogram uses six significant digits for its text labels.
        # Several distinct large bins can therefore have the same printed name.
        histogram[key] = histogram.get(key, 0) + int(count)
        label_resolution = max(label_resolution, 10 ** max(0, coordinate.as_tuple().exponent))
    return histogram, label_resolution


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
                  histogram_label_resolution_ns=1,
                  latency_end='Ramulator backend read completion',
                  guest_scope='all guest DRAM reads; instruction, data, OS and ROS clients')
    for metric, histogram_name, sum_name in [
            ('accepted', 'cpuReadLatencyNs', 'cpuReadLatencyTicks'),
            ('total', 'cpuReadTotalLatencyNs', 'cpuReadTotalLatencyTicks')]:
        histogram, label_resolution = latency_histogram(
            directory / 'stats.txt', prefix + '.' + histogram_name)
        if sum(histogram.values()) != samples:
            raise ValueError(f'{histogram_name} sample count mismatch: '
                             f'{sum(histogram.values())} bins vs {samples:g} reads; inspect stats.txt')
        result['histogram_label_resolution_ns'] = max(
            result['histogram_label_resolution_ns'], label_resolution)
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
    result['read_latency_tracking_version'] = int(get('readLatencyTrackingVersion'))
    result['admission_latency_valid'] = result['read_latency_tracking_version'] >= 2
    if not result['admission_latency_valid']:
        result['admission_latency_warning'] = (
            'Legacy Ruby packet retries can reuse another request first-attempt timestamp; '
            'admission-inclusive latency is unverified. Post-admission latency is separate.')
    if get('nodeAttributionEnabled'):
        names = ('unattributed', 'perception', 'planning', 'control',
                 'guest_bridge', 'kernel', 'other_user')
        nodes = {}
        accepted_sum = total_sum = retries_sum = 0
        for name in names:
            node_prefix = prefix + '.' + name + '.'
            field = lambda key: stats.get(node_prefix + key, 0)
            if node_prefix + 'reads' not in stats:
                raise ValueError('Missing per-node DRAM counters: ' + name)
            count = int(field('reads'))
            node = dict(samples=count, retries=int(field('retries')),
                        read_fraction=count / samples if samples else None)
            for metric, bins, ticks in (('accepted', 'acceptedNs', 'acceptedTicks'),
                                        ('total', 'totalNs', 'totalTicks')):
                histogram, resolution = latency_histogram(directory / 'stats.txt',
                                                          node_prefix + bins)
                if sum(histogram.values()) != count:
                    raise ValueError('Per-node histogram sample count mismatch: ' + name)
                node[metric + '_mean_ns'] = field(ticks) / count / frequency * 1e9 if count else None
                node[metric + '_p95_ns'] = percentile(histogram, .95)
                node[metric + '_p99_ns'] = percentile(histogram, .99)
                node[metric + '_histogram_ns'] = [[key, value] for key, value in sorted(histogram.items())]
                node[metric + '_label_resolution_ns'] = resolution
            accepted_sum += field('acceptedTicks')
            total_sum += field('totalTicks')
            retries_sum += node['retries']
            nodes[name] = node
        if (sum(n['samples'] for n in nodes.values()) != samples or
                accepted_sum != get('cpuReadLatencyTicks') or
                total_sum != get('cpuReadTotalLatencyTicks') or
                retries_sum != get('cpuRetries')):
            raise ValueError('Per-node counters do not reconcile with aggregate DRAM statistics')
        result['nodes'] = nodes
        result['node_scope'] = (
            'DRAM read transactions attributed to the initiating miss process, '
            'including its user-space ROS/DDS threads; coalesced misses count once. '
            'Kernel execution, other user processes, and untagged traffic are separate. '
            'Retries include reads and writes; cached accesses and host activity are excluded.')
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


def validate_node_registration(metrics, log_path):
    """Distinguish a genuinely empty DRAM bucket from missing instrumentation."""
    if 'nodes' not in metrics:
        raise ValueError('Per-node DRAM statistics were not exported')
    required = {'perception', 'planning', 'control', 'guest_bridge'}
    registered = set(re.findall(r'ROS_DRAM_REGISTER role=(\w+) address_space=',
                               Path(log_path).read_text(errors='replace')))
    if required - registered:
        raise ValueError('Missing DRAM node registration: ' + ', '.join(sorted(required - registered)))
    metrics['registered_node_roles'] = sorted(required)
