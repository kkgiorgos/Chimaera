#!/usr/bin/env python3
"""Validate diagnostic attempts and describe unloaded variability without a pass threshold."""
import argparse
from pathlib import Path

from aggregate import collect
from common import write_json


CONTROLS = ('joint_states', 'effort_command', 'clock')


def report(root):
    suite, attempts = collect(root)
    boundary_evidence = []
    for row in attempts:
        if (row['status'] == 'complete' and
                row.get('config', {}).get('benchmark_parameters', {}).get('trace_creation')):
            for name, metric in (row.get('metrics') or {}).items():
                if name == 'stereo_pair':
                    continue
                if (metric.get('creation_to_callback', {}).get('count', 0) != metric['received'] or
                        metric.get('preparation', {}).get('count', 0) != metric['published']):
                    row['errors'].append('Incomplete creation/preparation timestamp coverage: ' + name)
        mode = row.get('config', {}).get('middleware_copy_mode')
        if row['status'] != 'complete' or not mode:
            continue
        prefix = 'board.chimaera_m5.functionalCopies.'
        dumps = [{key: value for key, value in dump.items() if key.startswith(prefix)}
                 for dump in row.get('guest_statistics', {}).get('completed_dumps', [])]
        required = tuple(prefix + name for name in (
            'copyCalls', 'copyBytes', 'fillCalls', 'fillBytes', 'faults'))
        counters_present = bool(dumps) and all(
            all(key in dump and isinstance(dump[key], (int, float)) and dump[key] >= 0
                for key in required) for dump in dumps)
        active = bool(any(dump.get(prefix + 'copyBytes', 0) or dump.get(prefix + 'fillBytes', 0)
                          for dump in dumps))
        faults = any(dump.get(prefix + 'faults', 0) for dump in dumps)
        consistent = all(bool(dump.get(prefix + operation + 'Calls', 0)) ==
                         bool(dump.get(prefix + operation + 'Bytes', 0))
                         for dump in dumps for operation in ('copy', 'fill'))
        valid = counters_present and consistent and active == (mode == 'functional') and not faults
        boundary_evidence.append(dict(case=row['case']['name'], mode=mode, valid=valid,
            observed_copy_activity=active, memory_faults_observed=faults, dumps=dumps,
            scope='Exact raw statistics dumps; may overlap and include warmup/drain/export. Do not sum.'))
        if not valid:
            row['errors'].append('Experimental memory-cost boundary lacks valid copy-counter evidence')
    completed = [row for row in attempts if row['status'] == 'complete'
                 and not row['errors'] and row.get('metrics')]
    variability = []
    contrasts = []
    latency_variability, latency_contrasts = [], []
    groups = sorted({(row['case'].get('execution_layout'),
                      row['config'].get('middleware_copy_mode', 'guest')) for row in completed})
    for layout, mode in groups:
        baseline = [row for row in completed if row['case'].get('cell') == 'ball'
                    and row['case'].get('execution_layout') == layout
                    and row['config'].get('middleware_copy_mode', 'guest') == mode]
        for stream in CONTROLS:
            metrics = [row['metrics'][stream] for row in baseline]
            values = [metric['age']['p99_ms'] for metric in metrics
                      if metric['age']['p99_ms'] is not None]
            variability.append(dict(layout=layout, middleware_copy_mode=mode,
                stream=stream, windows=len(baseline),
                per_window_age_p99_ms=values, age_p99_min_ms=min(values) if values else None,
                age_p99_max_ms=max(values) if values else None,
                missed_source_releases=[metric['missed_source_releases'] for metric in metrics],
                missing_at_drain=[metric['missing_at_drain'] for metric in metrics],
                counts=[metric['received'] for metric in metrics]))
        for stream in CONTROLS:
            for boundary in ('creation_to_callback', 'latency'):
                values = [row['metrics'][stream].get(boundary, {}).get('p99_ms') for row in baseline]
                recorded = [value for value in values if value is not None]
                latency_variability.append(dict(layout=layout, middleware_copy_mode=mode,
                    stream=stream, boundary=boundary, windows=len(baseline),
                    recorded_windows=len(recorded), per_window_p99_ms=values,
                    p99_min_ms=min(recorded) if recorded else None,
                    p99_max_ms=max(recorded) if recorded else None))
        for loaded in completed:
            if (loaded['case'].get('cell') != 'bulk8m'
                    or loaded['case'].get('execution_layout') != layout
                    or loaded['config'].get('middleware_copy_mode', 'guest') != mode):
                continue
            for stream in CONTROLS:
                age = loaded['metrics'][stream]['age']['p99_ms']
                baseline_values = [row['metrics'][stream]['age']['p99_ms'] for row in baseline
                                   if row['metrics'][stream]['age']['p99_ms'] is not None]
                contrasts.append(dict(case=loaded['case']['name'], layout=layout,
                    middleware_copy_mode=mode, stream=stream,
                    loaded_age_p99_ms=age, baseline_per_window_age_p99_ms=baseline_values,
                    loaded_minus_baseline_age_p99_ms=[age - value for value in baseline_values]
                        if age is not None else [],
                    loaded_missed_source_releases=loaded['metrics'][stream]['missed_source_releases'],
                    loaded_missing_at_drain=loaded['metrics'][stream]['missing_at_drain']))
                for boundary in ('creation_to_callback', 'latency'):
                    value = loaded['metrics'][stream].get(boundary, {}).get('p99_ms')
                    unloaded = [row['metrics'][stream].get(boundary, {}).get('p99_ms')
                                for row in baseline]
                    latency_contrasts.append(dict(case=loaded['case']['name'], layout=layout,
                        middleware_copy_mode=mode, stream=stream, boundary=boundary,
                        loaded_p99_ms=value, baseline_per_window_p99_ms=unloaded,
                        loaded_minus_baseline_p99_ms=[value - b for b in unloaded if b is not None]
                            if value is not None else []))
    recorded_limits = suite['matrix']['limitations']
    limits = list(recorded_limits)
    if any(row.get('config', {}).get('middleware_copy_mode') == 'functional' for row in attempts):
        # Older copy matrices inherited this ordinary-mode-only description.
        # Preserve the frozen wording separately; do not change raw evidence.
        limits = [item.replace(
            'Guest serialization and DDS work remain modeled; no host middleware offload.',
            'Ordinary mode retains guest DDS memory work; functional-mode costs are stated separately.')
            for item in limits]
    return dict(schema_version=1, suite=str(root), stage=suite['matrix']['stage'],
        attempts=attempts, unloaded_variability=variability, load_contrasts=contrasts,
        unloaded_latency_variability=latency_variability, load_latency_contrasts=latency_contrasts,
        copy_boundary_evidence=boundary_evidence,
        acceptance='No numerical latency threshold selected; descriptive diagnostic only.',
        limitations=limits, recorded_matrix_limitations=recorded_limits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.suite)
    write_json(args.output, result)
    for row in result['attempts']:
        print(row['case']['name'], row['status'], '; '.join(row['errors']))


if __name__ == '__main__':
    main()
