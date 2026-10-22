#!/usr/bin/env python3
"""Export every planned attempt, rejecting mismatched evidence without hiding it."""
import argparse
import configparser
import csv
import math
from pathlib import Path
import random
import re

from common import (COMMON_FILES, match_key,
                    read_json, sha256, validate_config, write_json)
from metrics import analyze, read_events


def cpu_evidence(directory, config):
    origin_path = directory / 'gem5/latency_clock_origin.json'
    origin = read_json(origin_path, read_json(directory / 'clock-origin.json', {}))
    expected = 'TimingSimple' if config['cpu_type'] == 'timing' else 'Kvm'
    cpus = origin.get('active_cpus', [])
    if len(cpus) != 2 or any(expected not in cpu for cpu in cpus):
        raise ValueError('Active CPU origin does not establish requested two-core model')
    if origin.get('cpu_clock') != '3GHz' or origin.get('cpu') != config['cpu_type']:
        raise ValueError('CPU origin clock/model mismatch')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    with (directory / 'gem5/config.ini').open() as handle:
        parser.read_file(handle)
    if parser.get('board.clk_domain', 'clock') != '333':
        raise ValueError('Deployed clock is not gem5 3GHz (333ps ticks)')
    dram = parser['board.memory.mem_ctrl.dram']
    if dram['range'] != '0:3221225472' or dram['tCK'] != '1250' or dram['burst_length'] != '8':
        raise ValueError('Deployed memory does not match 3GiB DDR3_1600 geometry')
    if config['cpu_type'] == 'timing':
        if not any(parser.get(section, 'type', fallback='') == 'MESI_Two_Level_L1Cache_Controller'
                   for section in parser.sections()):
            raise ValueError('Missing MESI_TWO_LEVEL cache controllers')
        for suffix, count, size, assoc in (('L1Icache', 2, 16384, 8),
                                          ('L1Dcache', 2, 16384, 8),
                                          ('L2cache', 1, 262144, 16)):
            sections = [section for section in parser.sections() if section.endswith('.' + suffix)]
            if len(sections) != count or any(parser.getint(section, 'size') != size or
                                            parser.getint(section, 'assoc') != assoc for section in sections):
                raise ValueError('Deployed cache geometry differs: ' + suffix)
    return dict(origin=origin, config_ini_sha256=sha256(directory / 'gem5/config.ini'),
                interpretation='Active CPU list, not dormant KVM boot CPUs; DDR3 timing fields checked.')


def gem5_stats(path):
    blocks, current = [], None
    if not path.is_file():
        return dict(available=False, reason='No retained gem5 statistics')
    for line in path.read_text().splitlines():
        if 'Begin Simulation Statistics' in line:
            current = {}
        elif 'End Simulation Statistics' in line and current is not None:
            blocks.append(current)
            current = None
        elif current is not None:
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            if name in ('simSeconds', 'simInsts', 'simOps', 'hostSeconds') or (
                    'board.memory.' in name and re.search(r'bytes(Read|Written)|num(Read|Write)', name)) or (
                    re.search(r'board\.processor\..*\.(numCycles|numInsts|numOps)$', name)):
                try:
                    value = float(parts[1])
                    current[name] = value if math.isfinite(value) else None
                except ValueError:
                    pass
    return dict(available=bool(blocks), completed_dumps=blocks, partial_final_dump=current,
        sha256=sha256(path), scope='Exact retained gem5 dump scope. These are not assumed to be '
            'source-window-only counters; warmup/drain/export may be included. Repeated '
            'dumps may overlap; never sum them or sum aliases of instruction/memory counters.')


def validate_attempt(directory, case, attempt, suite):
    if attempt.get('input_sha256') != suite['input_sha256']:
        raise ValueError('Attempt input hashes differ from frozen suite')
    if attempt.get('physical_host') != suite['lock']['physical_host'] or not attempt.get('fresh_boot'):
        raise ValueError('Physical host or fresh-boot provenance differs')
    config = read_json(directory / 'run.json')
    validate_config(config)
    if config.get('status') != 'complete' or not (directory / 'complete').is_file():
        raise ValueError('Driver/export completion evidence missing')
    if config.get('boot_reused') or config.get('single_boot'):
        raise ValueError('Guest reuse is prohibited')
    for key, value in case['config'].items():
        if config.get(key) != value:
            raise ValueError('Requested/actual config mismatch: ' + key)
    selection = suite['lock']['variants'][case['implementation']]
    if not selection.get('ready'):
        raise ValueError('Frozen source/runtime selection was not ready')
    if config.get('transport_backend') != selection['backend'] or (
            config.get('middleware_transport') != selection['middleware']):
        raise ValueError('Measured backend differs from selected implementation')
    actual = {}
    for path, digest in config.get('artifact_sha256', {}).items():
        basename = Path(path).name
        if basename in actual and actual[basename] != digest:
            raise ValueError('Ambiguous artifact hash: ' + basename)
        actual[basename] = digest
    for relative in COMMON_FILES:
        if actual.get(Path(relative).name) != selection['common_files'][relative]:
            # run_case historically omits the two guest-clock libraries; the
            # immutable runtime and bundle lock still retain their byte hashes.
            if 'gem5_clock' not in relative or actual.get(Path(relative).name) is not None:
                raise ValueError('Endpoint/clock/RMW identity missing or differs: ' + relative)
    if actual.get('latency_driver') != selection['implementation_files']['lib/chimaera_ros_bridge/latency_driver']:
        raise ValueError('Per-implementation host driver identity missing or differs')
    if actual.get(Path(selection['bundle']['path']).name) != selection['bundle']['sha256']:
        raise ValueError('Guest bundle hash missing or differs')
    if actual.get('dds.xml') != sha256(directory / 'dds.xml'):
        raise ValueError('DDS profile hash missing or differs')
    evidence = cpu_evidence(directory, config) if config['mode'] == 'chimaera' else None
    return config, evidence


def collect(root):
    suite = read_json(root / 'suite.json')
    if not suite or suite['schema_version'] != 1:
        raise ValueError('Require runner suite.json, including frozen matrix and selection')
    attempts = []
    names = {case['name'] for case in suite['matrix']['cases']}
    for case in suite['matrix']['cases']:
        directory = root / case['name']
        attempt_error = None
        try:
            attempt = read_json(directory / 'benchmark-attempt.json', {})
            if not isinstance(attempt, dict):
                raise ValueError('Attempt record is not an object')
        except (OSError, ValueError) as error:
            attempt, attempt_error = {}, str(error)
        row = dict(case=case, path=str(directory), status=attempt.get('status', 'not_run'),
                   provenance=attempt, errors=[], metrics=None,
                   wall_seconds=attempt.get('wall_seconds'),
                   execution_wall_seconds=attempt.get('execution_wall_seconds'))
        attempts.append(row)
        if attempt_error:
            row['status'] = 'invalid'
            row['errors'].append('Unreadable attempt record: ' + attempt_error)
            continue
        if directory.exists() and not attempt:
            row['status'] = 'incomplete'
            row['errors'].append('Directory exists without final attempt record')
        if row['status'] != 'complete':
            if attempt.get('error'):
                row['errors'].append(attempt['error'])
            continue
        try:
            config, cpu = validate_attempt(directory, case, attempt, suite)
            events = read_events(directory / 'events.csv', config)
            row.update(config=config, cpu_evidence=cpu,
                metrics=analyze(events, config, suite['matrix']['statistics']),
                guest_statistics=gem5_stats(directory / 'gem5/stats.txt'),
                raw_sha256={name: sha256(directory / name) for name in
                            ('events.csv', 'run.json', 'benchmark-attempt.json', 'dds.xml', 'routes.json',
                             'guest-session.json', 'host.yaml', 'guest.yaml', 'driver.yaml')},
                wall_seconds=attempt.get('wall_seconds'))
            row['execution_wall_seconds'] = attempt.get('execution_wall_seconds')
            if config['mode'] == 'chimaera' and config['cpu_type'] == 'timing' and (
                    (not row['guest_statistics']['available'] or not any(
                        dump.get('simInsts', 0) > 0 and any('bytesRead' in key or 'bytesWritten' in key
                                                          for key in dump)
                        for dump in row['guest_statistics']['completed_dumps']))):
                row['errors'].append('Guest instruction/memory statistics unavailable')
        except (OSError, ValueError, KeyError, TypeError, configparser.Error) as error:
            row['status'] = 'invalid'
            row['errors'].append(str(error))
    # Preserve unexpected attempts as evidence, without adding them to matched data.
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and directory.name not in names and any(
                (directory / name).exists() for name in ('run.json', 'benchmark-attempt.json')):
            attempts.append(dict(path=str(directory), case=dict(name=directory.name),
                status='unplanned', errors=['Not in immutable matrix'], metrics=None))
    return suite, attempts


def _validate_native_delivery(peer, config, mode):
    for name, metric in peer['metrics'].items():
        if name == 'stereo_pair':
            continue
        if not metric['published'] or (config['delivery_policy'] == 'lossless' and (
                metric['missing_at_drain'] or metric['duplicates'] or metric['reordered'])):
            raise ValueError('Unusable native delivery reference: ' + mode + '/' + name)


def _shared_native_matches(row, attempts, suite):
    policy = suite['matrix']['native_references']
    if not isinstance(policy, dict) or policy.get('mode') != 'shared_native_free_shm' or (
            not isinstance(policy.get('by_cell'), dict)):
        raise ValueError('Unknown or malformed native reference policy')
    cell = row['case'].get('cell')
    declared = policy['by_cell'].get(cell)
    if not isinstance(declared, str) or not declared:
        raise ValueError('Missing declared shared native-free SHM reference for cell: ' + str(cell))
    peers = [peer for peer in attempts if peer['case'].get('name') == declared]
    if len(peers) != 1:
        raise ValueError('Require exactly one declared shared native-free SHM reference: ' + declared)
    peer = peers[0]
    if peer['status'] != 'complete' or not peer.get('metrics') or peer.get('errors') != []:
        raise ValueError('Declared shared native reference requires complete metrics and no errors')
    config, reference = row['config'], peer.get('config', {})
    if (peer['case'].get('cell') != cell or reference.get('mode') != 'native_free' or
            peer['case'].get('local_profile') != 'shm' or
            reference.get('repeat') != config['repeat'] or
            match_key(reference, native=True) != match_key(config, native=True)):
        raise ValueError('Declared shared native reference configuration differs')
    variants = suite.get('lock', {}).get('variants', {})
    owner = variants.get(peer['case'].get('implementation'), {}).get('common_files')
    measured = variants.get(row['case']['implementation'], {}).get('common_files')
    if (not isinstance(owner, dict) or set(owner) != set(COMMON_FILES) or owner != measured or
            any(not isinstance(digest, str) or not digest for digest in owner.values())):
        raise ValueError('Shared native reference endpoint/clock/RMW hashes missing or differ')
    try:
        same_dds = sha256(Path(peer['path']) / 'dds.xml') == sha256(Path(row['path']) / 'dds.xml')
    except OSError as error:
        raise ValueError('Shared native reference DDS profile missing') from error
    if not same_dds:
        raise ValueError('Shared native reference DDS profile differs')
    _validate_native_delivery(peer, config, 'native_free')
    return dict(native_free=declared)


def native_matches(row, attempts, suite):
    if 'native_references' in suite.get('matrix', {}):
        return _shared_native_matches(row, attempts, suite)
    config = row['config']
    variant = row['case']['implementation']
    matches = {}
    for mode in ('native_free', 'native_stepped'):
        peers = [p for p in attempts if p['status'] == 'complete' and p.get('metrics')
            and p['case'].get('implementation') == variant and p['config']['mode'] == mode
            and p['case']['local_profile'] == 'shm'
            and p['config']['repeat'] == config['repeat']
            and match_key(p['config'], native=True) == match_key(config, native=True)
            and sha256(Path(p['path']) / 'dds.xml') == sha256(Path(row['path']) / 'dds.xml')]
        if len(peers) != 1:
            raise ValueError('Require exactly one same-repeat matched SHM ' + mode + ' reference')
        peer = peers[0]
        _validate_native_delivery(peer, config, mode)
        matches[mode] = peer['case']['name']
    return matches


def compare(attempts, suite):
    rows = [r for r in attempts if r['status'] == 'complete' and r.get('metrics')]
    for row in rows:
        row['comparison_eligible'] = False
        config = row['config']
        if config['mode'] != 'chimaera':
            continue
        try:
            if row['case'].get('implementation') == 'initialized_m5':
                raise ValueError('Frozen final initialized pair is diagnostic, outside the main performance cohort')
            row['native_references'] = native_matches(row, attempts, suite)
            if config['cpu_type'] != 'timing':
                raise ValueError('KVM results are iteration evidence, not final comparisons')
            if config['verify_payload']:
                raise ValueError('Full-byte correctness cohort is separate from performance')
            if row['errors']:
                raise ValueError('Incomplete provenance or metrics')
            row['comparison_eligible'] = True
        except ValueError as error:
            row['comparison_exclusion'] = str(error)
    comparisons = []
    candidates = [r for r in rows if r['case'].get('implementation') == 'direct_dds_m5ops'
                  and r.get('comparison_eligible')]
    for candidate in candidates:
        for baseline in rows:
            if not baseline.get('comparison_eligible') or baseline['case']['implementation'] == 'direct_dds_m5ops':
                continue
            if match_key(baseline['config']) != match_key(candidate['config']) or (
                    baseline['config']['repeat'] != candidate['config']['repeat']):
                continue
            before = suite['lock']['variants'][baseline['case']['implementation']]
            after = suite['lock']['variants']['direct_dds_m5ops']
            if before['common_files'] != after['common_files']:
                candidate.setdefault('pair_exclusions', []).append(
                    'Cross-implementation endpoint/clock/RMW hashes differ from ' + baseline['case']['implementation'])
                continue
            planned_pair = [a for a in attempts if a['case'].get('implementation') in
                (baseline['case']['implementation'], 'direct_dds_m5ops')
                and a['case'].get('cell') == candidate['case']['cell']
                and a['case'].get('config', {}).get('mode') == 'chimaera'
                and match_key(a['case']['config']) == match_key(candidate['config'])]
            cohort_complete = all(a.get('comparison_eligible') and not a['errors'] for a in planned_pair)
            for name in candidate['metrics']:
                if name == 'stereo_pair':
                    continue
                old, new = baseline['metrics'][name], candidate['metrics'][name]
                valid = all(m['published'] > 0 and not m['missing_at_drain'] and not m['duplicates']
                    and not m['reordered'] for m in (old, new))
                same_source_count = old['published'] == new['published']
                comparisons.append(dict(baseline=baseline['case']['implementation'], stream=name,
                    cell=candidate['case']['cell'], policy=candidate['config']['delivery_policy'],
                    repeat=candidate['config']['repeat'], candidate_case=candidate['case']['name'],
                    baseline_case=baseline['case']['name'], delivery_valid=valid,
                    planned_pair_cases=len(planned_pair), cohort_complete=cohort_complete,
                    equal_source_count=same_source_count,
                    mean_change_percent=(100 * (new['latency']['mean_ms'] - old['latency']['mean_ms']) /
                        old['latency']['mean_ms'] if valid and same_source_count and
                        old['latency']['mean_ms'] not in (None, 0) else None),
                    baseline_p99_ms=old['latency']['p99_ms'], candidate_p99_ms=new['latency']['p99_ms'],
                    baseline_deadline_misses=old['received_age_deadline_misses'] + old['missing_age_deadline_misses'],
                    candidate_deadline_misses=new['received_age_deadline_misses'] + new['missing_age_deadline_misses'],
                    baseline_receipts=old['received'], candidate_receipts=new['received'],
                    baseline_bounded=old['bounded_observation'], candidate_bounded=new['bounded_observation']))
    return comparisons


def tail_decisions(comparisons, rules):
    groups = {}
    for row in comparisons:
        groups.setdefault((row['baseline'], row['cell'], row['policy'], row['stream']), []).append(row)
    decisions = []
    for key, rows in sorted(groups.items()):
        usable = [row for row in rows if row['delivery_valid'] and row['equal_source_count'] and
                  row['baseline_p99_ms'] is not None and row['candidate_p99_ms'] is not None]
        adequate = len(usable) >= rules['min_independent_windows'] and min(
            sum(row[side + '_receipts'] for row in usable) for side in ('baseline', 'candidate')) >= rules['min_control_receipts']
        upper = None
        if adequate:
            deltas = [row['candidate_p99_ms'] - row['baseline_p99_ms'] for row in usable]
            rng = random.Random(20261022)
            bootstraps = sorted(sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(10000))
            upper = bootstraps[9499]
        decisions.append(dict(baseline=key[0], cell=key[1], policy=key[2], stream=key[3],
            independent_windows=len(usable), adequate_counts=adequate,
            mean_paired_window_p99_delta_upper_95_ms=upper,
            no_added_deadline_misses=bool(usable) and all(r['candidate_deadline_misses'] <= r['baseline_deadline_misses'] for r in usable),
            bounded_observations=bool(usable) and all(r['baseline_bounded'] and r['candidate_bounded'] for r in usable),
            cohort_complete=all(r.get('cohort_complete', True) for r in rows),
            decision=('incomplete_cohort' if not all(r.get('cohort_complete', True) for r in rows) else
                'pass_observed_nonregression' if adequate and len(usable) == len(rows) and upper <= 0
                and all(r['candidate_deadline_misses'] <= r['baseline_deadline_misses'] for r in usable)
                and all(r['baseline_bounded'] and r['candidate_bounded'] for r in usable)
                else 'fail_observed_nonregression' if adequate else 'insufficient_evidence'),
            interpretation='One-sided percentile bootstrap of the mean paired per-window p99 difference; '
                'not a pooled p99, not an absolute worst-case bound. Five windows give limited '
                'independent tail evidence. No multiple-comparison correction is applied.'))
    return decisions


def load_assessments(attempts, rules):
    groups, loaded_controls = {}, []
    complete = [a for a in attempts if a['status'] == 'complete' and a.get('metrics')]
    for row in complete:
        config = row['config']
        if config['mode'] != 'chimaera' or config['verify_payload'] or config['cpu_type'] != 'timing':
            continue
        groups.setdefault((row['case']['implementation'], match_key(config)), []).append(row)
        parameters = config['benchmark_parameters']
        if not parameters['cameras_enabled'] and not parameters['bulk_bytes']:
            continue
        reference = dict(config, benchmark_parameters=dict(parameters,
            cameras_enabled=False, bulk_bytes=0, bulk_hz=1., bulk_burst=1, bulk_direction='both'))
        peers = [p for p in complete if p['case']['implementation'] == row['case']['implementation']
                 and p['config']['mode'] == 'chimaera' and p['config']['repeat'] == config['repeat']
                 and match_key(p['config']) == match_key(reference)]
        if len(peers) != 1:
            row['control_only_reference_exclusion'] = 'Need one same-repeat control-only case with matching DDS profile'
            continue
        row['control_only_reference'] = peers[0]['case']['name']
        for stream in ('joint_states', 'effort_command', 'clock'):
            loaded, unloaded = row['metrics'][stream], peers[0]['metrics'][stream]
            old, new = unloaded['latency']['p99_ms'], loaded['latency']['p99_ms']
            loaded_controls.append(dict(case=row['case']['name'], reference=peers[0]['case']['name'],
                stream=stream, loaded_p99_ms=new, unloaded_p99_ms=old,
                loaded_minus_unloaded_p99_ms=new - old if old is not None and new is not None else None,
                loaded_missing=loaded['missing_at_drain'], unloaded_missing=unloaded['missing_at_drain'],
                interpretation='Per-window loaded-control contrast; read with source counts, censoring and age deadlines.'))
    assessments = []
    for (implementation, identity), windows in groups.items():
        controls = ('joint_states', 'effort_command', 'clock')
        controls_n = min(sum(w['metrics'][s]['received'] for w in windows) for s in controls)
        independent = len({w['config']['repeat'] for w in windows})
        enough = independent >= rules['min_independent_windows'] and controls_n >= rules['min_control_receipts']
        bounded = all(m['bounded_observation'] for w in windows for name, m in w['metrics'].items()
                      if name != 'stereo_pair')
        lossless = windows[0]['config']['delivery_policy'] == 'lossless'
        assessments.append(dict(implementation=implementation, cell=windows[0]['case']['cell'],
            policy=windows[0]['config']['delivery_policy'], independent_windows=independent,
            minimum_control_receipts=controls_n, bounded_in_every_window=bounded,
            status='bounded_repeated_application_observations' if enough and bounded and lossless else
                   'insufficient_repeated_evidence' if not enough else 'loss_or_backlog_or_source_rate_failure',
            cases=[w['case']['name'] for w in windows],
            observed_goodput_by_window={s: [w['metrics'][s]['observation']['body_MB_per_observation_second']
                                           for w in windows] for s in windows[0]['metrics'] if s != 'stereo_pair'},
            limitation='Finite matched application observations at achieved offered load; no inference '
                       'of maximum capacity or unobserved internal queue/credit bounds.'))
    return assessments, loaded_controls


def export(root, output):
    suite, attempts = collect(root)
    comparisons = compare(attempts, suite)
    loads, loaded_controls = load_assessments(attempts, suite['matrix']['statistics'])
    coverage = {v: {status: sum(a['case'].get('implementation') == v and a['status'] == status
                for a in attempts) for status in ('complete', 'failed', 'cancelled', 'invalid', 'incomplete', 'not_run')}
                for v in suite['matrix']['requested_implementations']}
    report = dict(schema_version=1, physical_host=suite['lock']['physical_host'],
        stage=suite['matrix']['stage'], coverage=coverage,
        deferred_implementations=suite['matrix']['deferred_implementations'],
        attempts=attempts, comparisons=comparisons,
        load_assessments=loads, loaded_control_comparisons=loaded_controls,
        tail_decisions=tail_decisions(comparisons, suite['matrix']['statistics']),
        definitions=dict(source_rate='Unique finite source cohort, including drain, per source-second.',
            observed_rate='Unique callbacks in fixed interior receipt-time intervals per observation-second.',
            sustainability='No capacity claim from source-normalized receipts. Bounded observations '
                'must repeat at achieved load; internal queues/credits/wakeups are unavailable unless separately recorded.',
            model='m5ops is functional payload transfer, excluding timed DMA payload requests.',
            byte_accounting='Body bytes exclude serialization; event bytes are typed serialized sample bytes. '
                'Never add stereo pair and eye rows.',
            tails='Nearest-rank percentiles; every per-window distribution remains explicit.'),
        input_sha256=suite['input_sha256'])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'comparison.json', report)
    flat = []
    for attempt in attempts:
        base = dict(case=attempt['case']['name'], implementation=attempt['case'].get('implementation'),
                    status=attempt['status'], errors='; '.join(attempt['errors']),
                    comparison_eligible=attempt.get('comparison_eligible', False),
                    comparison_exclusion=attempt.get('comparison_exclusion'),
                    wall_seconds=attempt.get('wall_seconds'))
        base['execution_wall_seconds'] = attempt.get('execution_wall_seconds')
        if not attempt.get('metrics'):
            flat.append(dict(base, stream=None))
        for stream, metric in (attempt.get('metrics') or {}).items():
            row = dict(base, stream=stream, **{key: metric.get(key) for key in (
                'published', 'received', 'missing_at_drain', 'duplicates', 'reordered',
                'delivered_per_source_second', 'missed_source_releases', 'p99_tail_sample_count',
                'published_event_bytes', 'received_event_bytes', 'received_age_deadline_misses',
                'missing_age_deadline_misses', 'expected_scheduled_releases', 'source_rate_relative_error')})
            for kind in ('latency', 'age', 'release_lag', 'callback_skew'):
                for stat in ('count', 'mean_ms', 'p50_ms', 'p95_ms', 'p99_ms', 'max_ms'):
                    row[kind + '_' + stat] = metric.get(kind, {}).get(stat)
            observation = metric.get('observation', {})
            row.update(observed_MB_s=observation.get('body_MB_per_observation_second'),
                observed_messages_s=observation.get('messages_per_observation_second'),
                backlog_growth=observation.get('backlog_growth_messages'),
                oldest_age_growth_ms=observation.get('oldest_age_growth_ms'))
            flat.append(row)
    fields = list(dict.fromkeys(key for row in flat for key in row))
    with (output / 'comparison.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(flat)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = export(args.root, args.output)
    print(len(report['attempts']), 'visible attempts;', len(report['comparisons']), 'matched stream comparisons')


if __name__ == '__main__':
    main()
