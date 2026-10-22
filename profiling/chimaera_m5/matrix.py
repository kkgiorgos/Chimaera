#!/usr/bin/env python3
"""Prepare explicit staged cases; this command never imports a runtime or executes it."""
import argparse
import itertools
from pathlib import Path

from common import (BALL, DIAGNOSTIC_VARIANTS, GEOMETRY, PARAMETERS, PERFORMANCE_VARIANTS,
                    VARIANTS, validate_config, write_json)


def short_matrix():
    """Initial transient screen: ten guest cases and two shared free references."""
    cases = []
    variants = ['direct_dds_m5ops', 'scheduled_dds_dma',
                *[v for v in PERFORMANCE_VARIANTS
                  if v not in ('direct_dds_m5ops', 'scheduled_dds_dma')]]
    for cell, selected, bulk in (('short-ball', variants, False),
                                 ('short-bulk8m', variants[:2], True)):
        for variant in selected:
            parameters = dict(PARAMETERS)
            if bulk:
                parameters.update(bulk_bytes=8 * 1024**2, bulk_hz=5., bulk_burst=1,
                                  bulk_direction='both')
            config = dict(GEOMETRY, duration=.2, warmup=.05, drain=.1,
                timeout=1800, physics_step_ns=1000000, interval_us=2000, poll_us=1000,
                cpu_type='timing', single_boot=False, delivery_policy='lossless',
                verify_payload=False, reliability='reliable', depth=128,
                workload='ball_catching', ball_catching=dict(BALL),
                payload_bytes=480 * 360 * 3, rate_hz=30., repeat=1, mode='chimaera',
                benchmark_parameters=parameters, dds_tuning='large_shm_only',
                dds_shm_bytes=64 * 1024**2, dds_max_message_bytes=9 * 1024**2 - 4096)
            validate_config(config)
            cases.append(dict(name=f'{cell}-{variant}-chimaera-shm-r1',
                implementation=variant, cell=cell, cohort='performance',
                comparison_role='main', source_window_seconds=.2,
                local_profile='shm', config=config))
    references = {}
    for cell in ('short-ball', 'short-bulk8m'):
        source = next(c for c in cases if c['cell'] == cell
                      and c['implementation'] == 'direct_dds_m5ops')
        config = dict(source['config'], mode='native_free',
                      benchmark_parameters=dict(source['config']['benchmark_parameters']))
        name = cell + '-shared-native-free-shm-r1'
        cases.append(dict(source, name=name, config=config,
                          reference_role='shared_native_free_shm'))
        references[cell] = name
    return dict(schema_version=1, stage='short', cases=cases,
        requested_implementations=list(VARIANTS), selected_implementations=variants,
        main_performance_implementations=list(PERFORMANCE_VARIANTS),
        diagnostic_implementations=list(DIAGNOSTIC_VARIANTS),
        deferred_implementations=list(DIAGNOSTIC_VARIANTS),
        native_references=dict(mode='shared_native_free_shm', by_cell=references),
        statistics=dict(observation_bin_seconds=.025, trim_seconds=0.,
            bulk_observation_alignment='fixed_window',
            control_age_deadline_ms=10., image_age_deadline_ms=33.333334,
            min_control_receipts=10000, min_independent_windows=5,
            max_backlog_growth_messages=1, max_oldest_age_growth_ms=2.,
            max_relative_source_rate_error=.05),
        limitations=['Initial 0.2-second transient source windows; 0.05s warmup and 0.1s drain.',
            'No stepped native runs, latest-policy duplicates, UDP references or repeated windows.',
            'About six frames per eye and fifty joint/effort samples: report sample counts and descriptive latency.',
            'One scheduled 8MiB sample per direction inside the bulk source window, plus warmup traffic.',
            'Fixed receipt windows need not contain whole burst periods; throughput is transient, not sustainable capacity.',
            'Shared native-free SHM references require matching configuration, DDS profile and common binary hashes.',
            'Functional m5 payload transfer changes the memory-cost model relative to timed DMA.',
            'READY exact binary/source correspondence is unverified; FINAL remains a separate diagnostic.'])


def cells(stage):
    result = [('ball', dict(PARAMETERS), 0)]
    if stage in ('capacity', 'tails'):
        result.insert(0, ('control-only', dict(PARAMETERS, cameras_enabled=False), 0))
    if stage == 'capacity':
        for profile_body in (1024**2, 8 * 1024**2):
            result.append((f'control-only-profile-{profile_body}B',
                           dict(PARAMETERS, cameras_enabled=False), profile_body))
            result.append((f'ball-profile-{profile_body}B', dict(PARAMETERS), profile_body))
        for size, mib_s, burst, direction in itertools.product(
                (512 * 1024, 1024**2, 8 * 1024**2), (2, 8, 32), (1, 4), ('h2g', 'g2h', 'both')):
            name = f'bulk-{size}B-{mib_s}MiBs-b{burst}-{direction}'
            result.append((name, dict(PARAMETERS, bulk_bytes=size,
                bulk_hz=mib_s * 1024**2 / size, bulk_burst=burst, bulk_direction=direction), size))
    return result


def make_matrix(args):
    if args.stage == 'short':
        return short_matrix()
    variants = args.variants or (list(PERFORMANCE_VARIANTS) if args.stage == 'all-prior' else
                                ['scheduled_dds_dma', 'direct_dds_m5ops'])
    repeats = args.repeats or (5 if args.stage == 'tails' else 3 if args.stage == 'all-prior' else 1)
    duration = args.duration or (8. if args.stage == 'tails' else 1. if args.stage == 'smoke' else 2.)
    policies = args.policies or (['lossless', 'latest'] if args.stage == 'all-prior' else ['lossless'])
    cases = []
    selected_cells = cells(args.stage)
    if args.bulk_bytes:
        if args.bulk_mib_s is None or args.bulk_mib_s <= 0:
            raise ValueError('Selected tail/capacity load needs positive --bulk-mib-s')
        selected_cells.append(('selected-bulk', dict(PARAMETERS, bulk_bytes=args.bulk_bytes,
            bulk_hz=args.bulk_mib_s * 1024**2 / args.bulk_bytes,
            bulk_burst=args.bulk_burst, bulk_direction=args.bulk_direction), args.bulk_bytes))
        if args.stage == 'tails' and args.bulk_bytes >= 1024**2:
            selected_cells.append(('control-only-selected-profile',
                dict(PARAMETERS, cameras_enabled=False), args.bulk_bytes))
            selected_cells.append(('ball-selected-profile', dict(PARAMETERS), args.bulk_bytes))
    for cell, parameters, profile_body in selected_cells:
        if args.cells and cell not in args.cells:
            continue
        cell_duration = max(duration, 3 * parameters['bulk_burst'] / parameters['bulk_hz']
                            if parameters['bulk_bytes'] else duration)
        for policy, repeat in itertools.product(policies, range(1, repeats + 1)):
            order = list(variants) if repeat % 2 else list(reversed(variants))
            for variant in order:
                for mode, local in (('native_free', 'shm'), ('native_stepped', 'shm'),
                                    ('native_free', 'udp'), ('native_stepped', 'udp'),
                                    ('chimaera', 'shm')):
                    config = dict(GEOMETRY, duration=cell_duration, warmup=.25, drain=.75,
                        timeout=14400, physics_step_ns=1000000, interval_us=2000, poll_us=1000,
                        cpu_type='kvm' if args.stage == 'smoke' else 'timing',
                        single_boot=False, delivery_policy=policy, verify_payload=args.correctness,
                        reliability='reliable', depth=128, workload='ball_catching',
                        ball_catching=dict(BALL), payload_bytes=480 * 360 * 3, rate_hz=30.,
                        repeat=repeat, mode=mode, benchmark_parameters=dict(parameters),
                        dds_tuning='large_shm_only' if local == 'shm' else 'udp_loopback',
                        dds_shm_bytes=64 * 1024**2 if profile_body >= 8 * 1024**2 else 16 * 1024**2,
                        dds_max_message_bytes=(9 * 1024**2 - 4096 if profile_body >= 8 * 1024**2
                            else 2 * 1024**2 if profile_body >= 1024**2 else 1024**2))
                    if local == 'udp':
                        config['dds_shm_bytes'] = 0
                    validate_config(config)
                    name = f'{cell}-{policy}-{variant}-{mode}-{local}-r{repeat}'
                    cases.append(dict(name=name, implementation=variant, cell=cell,
                        cohort='correctness' if args.correctness else 'performance',
                        comparison_role='diagnostic' if variant in DIAGNOSTIC_VARIANTS else 'main',
                        source_window_seconds=cell_duration,
                        local_profile=local, config=config))
    if not cases:
        raise ValueError('No cases selected')
    return dict(schema_version=1, stage=args.stage, cases=cases,
        requested_implementations=list(VARIANTS), selected_implementations=variants,
        main_performance_implementations=list(PERFORMANCE_VARIANTS),
        diagnostic_implementations=list(DIAGNOSTIC_VARIANTS),
        deferred_implementations=[v for v in VARIANTS if v not in variants],
        statistics=dict(observation_bin_seconds=.25, trim_seconds=.25,
            control_age_deadline_ms=10., image_age_deadline_ms=33.333334,
            min_control_receipts=10000, min_independent_windows=5,
            max_backlog_growth_messages=1, max_oldest_age_growth_ms=2.,
            max_relative_source_rate_error=.05),
        limitations=['KVM is iteration only; final numbers require active TimingSimpleCPU.',
            'Nine identities: eight main variants and a separately retained final-pair diagnostic.',
            'Full matrices are staged plans, not claims that every case has run.',
            'No verifier-disabled inference is made for historical data.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('smoke', 'all-prior', 'capacity', 'tails', 'short'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--variants', choices=VARIANTS, nargs='+')
    parser.add_argument('--policies', choices=('lossless', 'latest'), nargs='+')
    parser.add_argument('--cells', nargs='+', help='Explicit cell subset; ball/control-only are useful first')
    parser.add_argument('--duration', type=float)
    parser.add_argument('--repeats', type=int)
    parser.add_argument('--correctness', action='store_true')
    parser.add_argument('--bulk-bytes', type=int, help='Additional selected-bulk cell, e.g. a measured knee')
    parser.add_argument('--bulk-mib-s', type=float, help='Selected bulk offered MiB/s per direction')
    parser.add_argument('--bulk-burst', type=int, default=1)
    parser.add_argument('--bulk-direction', choices=('h2g', 'g2h', 'both'), default='both')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite a matrix')
    if args.stage == 'short' and any((args.variants, args.policies, args.cells, args.duration,
                                    args.repeats, args.correctness, args.bulk_bytes,
                                    args.bulk_mib_s, args.bulk_burst != 1,
                                    args.bulk_direction != 'both')):
        parser.error('The short stage has a fixed approved 12-case configuration')
    if args.stage != 'short' and args.duration is not None and args.duration <= .5:
        parser.error('Observation requires more than two trim intervals (0.5s)')
    if args.repeats is not None and args.repeats < 1:
        parser.error('Positive repetition count required')
    matrix = make_matrix(args)
    write_json(args.output, matrix)
    print(len(matrix['cases']), 'planned cases; no benchmark executed')


if __name__ == '__main__':
    main()
