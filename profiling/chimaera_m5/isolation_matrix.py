#!/usr/bin/env python3
"""Prepare the bounded executor-isolation diagnostic; does not launch runs."""
import argparse
from copy import deepcopy
from pathlib import Path

from common import validate_config, write_json
from matrix import short_matrix


def isolation_matrix(cpu_type):
    template = next(case for case in short_matrix()['cases']
                    if case['name'] == 'short-ball-direct_dds_m5ops-chimaera-shm-r1')
    cases = []
    # Establish unloaded variability first, interleaving layouts to limit drift.
    # Timing runs retain the established short source window to fit a first
    # bounded diagnostic. KVM uses longer windows and full payload checking.
    for loaded, repeats in ((False, 3 if cpu_type == 'timing' else 1), (True, 1)):
        for repeat in range(1, repeats + 1):
            for isolated in (False, True):
                case = deepcopy(template)
                config = case['config']
                config.update(cpu_type=cpu_type, verify_payload=cpu_type == 'kvm', repeat=repeat)
                if cpu_type == 'kvm':
                    config.update(duration=1., warmup=.2, drain=.5, timeout=600)
                config['benchmark_parameters'].update(
                    isolate_bulk_execution=isolated, trace_publish=True, trace_creation=True)
                if loaded:
                    config['benchmark_parameters'].update(bulk_bytes=8 * 1024**2, bulk_hz=5.)
                label = 'split' if isolated else 'merged'
                cell = 'bulk8m' if loaded else 'ball'
                case.update(name=f'isolation-{cell}-{label}-{cpu_type}-r{repeat}',
                            cell=cell, execution_layout=label, comparison_role='diagnostic',
                            cohort='correctness' if cpu_type == 'kvm' else 'instrumented_diagnostic')
                validate_config(config)
                cases.append(case)
    rules = deepcopy(short_matrix()['statistics'])
    return dict(schema_version=1, stage='executor_isolation', cases=cases, statistics=rules,
                limitations=[
                    'One DDS participant per side in both layouts; split adds one application thread.',
                    'Guest serialization and DDS work remain modeled; no host middleware offload.',
                    'Publish timing is opt-in instrumentation, shared by both layouts.',
                    'Short timing windows establish descriptive repeat variability, not tail guarantees.',
                    'Supplemental 8MiB traffic is a transient diagnostic, not a saturation measurement.',
                    'No numeric latency allowance is predeclared; examine unloaded variability first.'])


def copy_mode_matrix(cpu_type, unloaded_repeats=1):
    if type(unloaded_repeats) is not int or not 1 <= unloaded_repeats <= (3 if cpu_type == 'timing' else 1):
        raise ValueError('Copy comparison supports 1–3 timing repeats or one KVM correctness window')
    matrix = isolation_matrix(cpu_type)
    cases = []
    for original in matrix['cases']:
        if original['execution_layout'] != 'split' or original['config']['repeat'] > unloaded_repeats:
            continue
        for mode in ('guest', 'functional'):
            case = deepcopy(original)
            case['config']['middleware_copy_mode'] = mode
            case['name'] = f'copies-{case["cell"]}-{mode}-{cpu_type}-r{case["config"]["repeat"]}'
            case['modeling_boundary'] = mode
            validate_config(case['config'])
            cases.append(case)
    matrix.update(stage='middleware_copy_boundary', cases=cases)
    matrix['limitations'][1] = (
        'Ordinary mode retains guest DDS memory work; functional-mode costs are stated separately.')
    matrix['limitations'].append(
        'Functional mode excludes large scoped middleware copy/fill instructions and memory accesses; '
        'application work, prefault stores, notification and the remaining middleware stay modeled.')
    return matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-type', choices=('kvm', 'timing'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--copy-modes', action='store_true')
    parser.add_argument('--unloaded-repeats', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to replace an existing matrix')
    if args.unloaded_repeats != 1 and not args.copy_modes:
        parser.error('--unloaded-repeats requires --copy-modes')
    write_json(args.output, copy_mode_matrix(args.cpu_type, args.unloaded_repeats)
               if args.copy_modes else isolation_matrix(args.cpu_type))


if __name__ == '__main__':
    main()
