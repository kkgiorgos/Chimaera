#!/usr/bin/env python3
"""Publish bounded diagnostic evidence, preserving incomplete and failed cases."""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from common import read_json, sha256, write_json
from isolation_render import render
from isolation_report import CONTROLS, report


def export(root, output):
    output.mkdir(parents=True, exist_ok=True)
    experiments = {}
    for name, relative in (('execution_kvm', 'kvm-r1'), ('execution_timing', 'timing-r1'),
                           ('copy_kvm', 'copy-modes/kvm-r1'),
                           ('copy_timing', 'copy-modes/timing-r1'),
                           ('stress_kvm', 'kvm-stress-r1')):
        directory = root / relative
        if (directory / 'suite.json').is_file():
            experiments[name] = report(directory)
    robot = []
    for directory in sorted(root.glob('actual-robot-kvm-r*')):
        if not directory.is_dir():
            continue
        result = read_json(directory / 'baseline/result.json')
        log = directory / 'baseline/launch.log'
        error = None
        if result is None and log.is_file():
            lines = log.read_text(errors='replace').splitlines()
            error = next((line.strip() for line in lines if 'RuntimeError: guest finished' in line), None)
        if result is None and directory.name.endswith('r1'):
            error = 'Scene validation failed in the small SDK: missing urdf_parser_plugin metadata'
        robot.append(dict(path=str(directory), result=result, startup_error=error,
                          inputs=read_json(directory / 'inputs.json')))
    summary = dict(generated_utc=datetime.now(timezone.utc).isoformat(), experiments=experiments,
        actual_robot=robot, followup=read_json(root / 'followup-status.json'),
        deferred=read_json(root / 'deferred-experiments.json'),
        round_budget=read_json(root / 'round-budget.json'),
        validation=read_json(root / 'validation.json'),
        exporter_sha256=sha256(Path(__file__)),
        conclusion='Application execution isolation removes the observed control stall in the tested '
                   'transient. Camera/bulk isolation and maximum sustainable throughput are not established.',
        limits=['No numerical latency regression allowance selected.',
                'Short, instrumented timing windows; descriptive per-window p99/max only.',
                'Functional-copy mode is a separate memory-cost model.',
                'Actual sample creation/capture is not separately timestamped by this benchmark.',
                'The actual two-core robot application has no successful validation.',
                'Per-topic priority, mixed delivery policies and reserved flow credits remain future work.'])
    write_json(output / 'summary.json', summary)
    fields = ['experiment', 'case', 'status', 'layout', 'copy_mode', 'stream', 'published', 'received',
              'missed_source_releases', 'missing_at_drain', 'duplicates', 'reordered',
              'creation_to_callback_p99_ms', 'creation_to_callback_max_ms', 'preparation_max_ms',
              'publish_to_callback_p99_ms', 'publish_to_callback_max_ms', 'age_p50_ms', 'age_p99_ms', 'age_max_ms', 'release_lag_max_ms', 'publish_call_max_ms',
              'receipt_window_body_MB_s', 'errors']
    with (output / 'metrics.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for experiment, result in experiments.items():
            for row in result['attempts']:
                base = dict(experiment=experiment, case=row['case']['name'], status=row['status'],
                            layout=row['case'].get('execution_layout'),
                            copy_mode=row.get('config', {}).get('middleware_copy_mode', 'guest'),
                            errors='; '.join(row['errors']))
                if not row.get('metrics'):
                    writer.writerow(base)
                    continue
                for stream, metric in row['metrics'].items():
                    if stream == 'stereo_pair':
                        continue
                    writer.writerow(dict(base, stream=stream, **{key: metric[key] for key in (
                        'published', 'received', 'missed_source_releases', 'missing_at_drain',
                        'duplicates', 'reordered')},
                        creation_to_callback_p99_ms=metric['creation_to_callback']['p99_ms'],
                        creation_to_callback_max_ms=metric['creation_to_callback']['max_ms'],
                        preparation_max_ms=metric['preparation']['max_ms'],
                        publish_to_callback_p99_ms=metric['latency']['p99_ms'],
                        publish_to_callback_max_ms=metric['latency']['max_ms'], age_p50_ms=metric['age']['p50_ms'],
                        age_p99_ms=metric['age']['p99_ms'], age_max_ms=metric['age']['max_ms'],
                        release_lag_max_ms=metric['release_lag']['max_ms'],
                        publish_call_max_ms=metric['publish_call']['max_ms'],
                        receipt_window_body_MB_s=metric['observation']['body_MB_per_observation_second']))
    lines = ['# DDS m5ops execution and memory-cost diagnostics, 2026-10-23', '',
        summary['conclusion'], '',
        'Two guest cores, 3 GHz, 16 KiB L1I/L1D, 256 KiB L2, 3 GiB RAM. Timing uses '
        'active BaseTimingSimpleCPU cores; KVM correctness is separate. Stereo is 480x360 '
        'RGB at 30 Hz/eye, joints/effort 250 Hz, clock 1 kHz. Timing source/warmup/drain '
        'are 0.2/0.05/0.1 s, with one measured 8 MiB sample/direction in loaded cases.', '',
        '[Interactive report](report.html), [full evidence](summary.json), [per-stream CSV](metrics.csv).', '',
        'The primary requirement is actual sample creation/capture to callback entry. This '
        'benchmark preallocates and reuses payloads and lacks an actual creation timestamp. '
        'Its `latency` is publish-call-entry to callback; `age` additionally includes source '
        'scheduling delay. Neither is relabeled as the complete creation-to-callback metric. '
        'Both memory-cost modes are retained separately. Functional mode deliberately excludes '
        'scoped memory-copy costs, which need calibration/representation for a physical offload target.', '',
        '## Execution isolation', '',
        'Both layouts share one DDS participant and unchanged transport pools. Split execution '
        'adds one application executor for image/bulk publication and typed takes. The first '
        'diagnostic also has a second 1 kHz source timer; its instruction cost is included.', '',
        '| Control | Merged unloaded p99 range | Merged +8 MiB p99 | Split unloaded p99 range | Split +8 MiB p99 |',
        '|---|---:|---:|---:|---:|']
    execution = experiments.get('execution_timing', {})
    rows = execution.get('attempts', [])
    def p99(layout, cell, stream):
        return [row['metrics'][stream]['age']['p99_ms'] for row in rows
                if row['status'] == 'complete' and not row['errors'] and row.get('metrics')
                and row['case'].get('execution_layout') == layout and row['case'].get('cell') == cell]
    def display(values):
        values = [value for value in values if value is not None]
        if not values:
            return 'unavailable'
        return (f'{min(values):.3f}–{max(values):.3f}' if len(values) > 1 else f'{values[0]:.3f}') + ' ms'
    for stream in CONTROLS:
        lines.append('| ' + stream + ' | ' + ' | '.join(display(p99(layout, cell, stream))
            for layout, cell in (('merged', 'ball'), ('merged', 'bulk8m'),
                                 ('split', 'ball'), ('split', 'bulk8m'))) + ' |')
    lines += ['', 'The merged loaded case directly records about 76 ms inside guest bulk publication '
        'and twelve missing scheduled effort releases. Split execution keeps measured control age '
        'within the observed unloaded range and emits those effort releases. Cameras still reach '
        'about 102–104 ms age; bulk delivery is slower. This is control protection, not full flow isolation.', '',
        'The nominal final raw instruction dumps are about 64–68 million for merged execution and '
        '71–79 million for split execution. These retain their original scopes (warmup/drain/export '
        'may be included); they are not source-window-only costs and are not summed.', '',
        '## Separate middleware-copy model', '',
        'The optional helper offloads 64 KiB–32 MiB copies/fills only in marked RMW/receive scopes. '
        'Application work, prefault stores, calls and remaining middleware stay modeled. It creates '
        'no receive pool or worker thread. Both arms preload the same helper. Real KVM verification '
        'checks all Image bytes and model counters; faults or absent/incorrect activity reject an arm.', '']
    for name in ('copy_kvm', 'copy_timing'):
        result = experiments.get(name)
        if result:
            for row in result['attempts']:
                lines.append('- ' + row['case']['name'] + ': ' + row['status'] +
                             ('; ' + '; '.join(row['errors']) if row['errors'] else ''))
    copy_rows = experiments.get('copy_timing', {}).get('attempts', [])
    lines += ['', '| Mode / load | Left / right camera max | Joint / effort / clock p99 | Guest bulk publish max | Last raw instructions |',
              '|---|---:|---:|---:|---:|']
    for row in copy_rows:
        if row['status'] != 'complete' or row['errors'] or not row.get('metrics'):
            continue
        metrics = row['metrics']
        mode = row['config']['middleware_copy_mode']
        cell = row['case']['cell']
        camera = ' / '.join(f"{metrics[name]['age']['max_ms']:.3f}" for name in ('left_image', 'right_image'))
        controls = ' / '.join(f"{metrics[name]['age']['p99_ms']:.3f}" for name in CONTROLS)
        bulk = metrics.get('bulk_image_g2h')
        publish = f"{bulk['publish_call']['max_ms']:.3f} ms" if bulk else '—'
        dumps = row.get('guest_statistics', {}).get('completed_dumps', [])
        instructions = f"{dumps[-1]['simInsts']:,.0f}" if dumps and 'simInsts' in dumps[-1] else 'unavailable'
        lines.append(f'| {mode} / {cell} | {camera} ms | {controls} ms | {publish} | {instructions} |')
    lines += ['', 'Copy-mode comparisons use their own newly frozen simulator, DDS library and helper '
              'in both arms. Cross-phase differences from the execution-only experiment are not '
              'attributed to offload. Instruction and memory counters include their retained raw '
              'dump scopes; they do not isolate serialization or source-window work.', '']
    lines += ['| Loaded copy mode | Left / right publish-entry → callback max | Host → guest / guest → host bulk max |',
              '|---|---:|---:|']
    for row in copy_rows:
        if (row['status'] != 'complete' or row['errors'] or not row.get('metrics')
                or row['case']['cell'] != 'bulk8m'):
            continue
        metrics = row['metrics']
        camera = ' / '.join(f"{metrics[name]['latency']['max_ms']:.3f}" for name in ('left_image', 'right_image'))
        bulk = ' / '.join(f"{metrics[name]['latency']['max_ms']:.3f}" for name in ('bulk_image_h2g', 'bulk_image_g2h'))
        lines.append(f"| {row['config']['middleware_copy_mode']} | {camera} ms | {bulk} ms |")
    lines += ['', '## Actual application and remaining scope', '']
    budget = summary.get('round_budget')
    if budget:
        lines += [f"The simulation round stopped after {budget['elapsed_seconds']:.0f} host seconds "
                  f"within its {budget['cap_seconds']}-second cap, with no owned simulation containers running. "
                  'The final functional-copy unloaded case was stopped for budget exhaustion; '
                  'it provides no complete unloaded reference. No further simulations were started.', '']
    for item in robot:
        state = str(item['result']) if item['result'] is not None else item['startup_error'] or 'No scored result'
        lines.append('- ' + Path(item['path']).name + ': ' + state)
    lines += ['', 'Validation: 81 Python tooling checks pass. The native functional-copy contract '
              'test passes. Rebuilt DDS checks pass (four executed, one disabled trace test); '
              'eight fresh KVM diagnostic cases pass full-payload verification. The two-core '
              'actual robot validation remains unsuccessful.', '']
    lines += ['The subsequent creation-timestamp endpoint revision builds and its metric tests pass, '
              'but it has not run in a simulator. All retained measurements predate it; historical '
              'creation-latency fields remain empty. New host/guest endpoints must be frozen together '
              'before qualifying that extended record format.', '']
    lines += ['', 'Per-flow priorities, per-stream lossless/latest policies, flow-specific receive '
        'credits and independent camera/bulk service still need implementation/qualification. '
        'Idle and sparse bulk-source wakeups still need optimization and qualification; '
        'the published comparisons retain the measured 1 kHz timer behavior.', '',
        'No maximum/sustainable throughput or statistical latency non-regression claim follows '
        'from these short windows. Receipt-window rates exclude drain; finite-cohort delivery '
        'rates are a different metric. Missing releases, losses, duplicates, ordering, backlog '
        'and all incomplete/unstarted cases remain in the JSON/CSV.', '',
        'Raw runs and frozen identities: `.chimaera-m5/isolation-20261023/`. '
        'The two-hour round uses an absolute deadline and does not reset between suites.', '']
    (output / 'README.md').write_text('\n'.join(lines))
    render(root, output / 'report.html')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.root, args.output)


if __name__ == '__main__':
    main()
