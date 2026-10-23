#!/usr/bin/env python3
"""Publish creation-timestamp diagnostics and the actual two-core robot recovery."""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from common import read_json, sha256, write_json
from isolation_render import render
from isolation_report import CONTROLS, report


def export(root, output):
    output.mkdir(parents=True, exist_ok=True)
    suites = {stage: report(root / 'creation' / (stage + '-r1'))
              for stage in ('kvm', 'timing')
              if (root / 'creation' / (stage + '-r1') / 'suite.json').is_file()}
    robots = []
    for path in sorted(p for p in root.glob('actual-robot-kvm-r*') if p.is_dir()):
        directory = path / 'baseline'
        log = directory / 'launch.log'
        errors = [line.strip() for line in log.read_text(errors='replace').splitlines()
                  if 'RuntimeError:' in line] if log.is_file() else []
        robots.append(dict(name=path.name, result=read_json(directory / 'result.json'),
            cpu=read_json(directory / 'gem5/cpu_roi.json'),
            launch=read_json(directory / 'launch_validation.json'),
            postprocessing=read_json(directory / 'offline-postprocessing.json'),
            operator=read_json(root / ('actual-robot-' + path.name.rsplit('-', 1)[-1] + '-operator.json')),
            inputs=read_json(path / 'inputs.json'), errors=errors))
    summary = dict(schema_version=1, generated_utc=datetime.now(timezone.utc).isoformat(),
        budget=read_json(root / 'round-budget.json'), followup=read_json(root / 'followup-status.json'),
        suites=suites, actual_robot=robots, actual_gate=read_json(root / 'two-core-catch-pass.json'),
        validation=read_json(root / 'validation.json'),
        delivery_contract=read_json(root / 'delivery-contract.json'),
        extra_correctness=read_json(root / 'extra-correctness.json'),
        composition=read_json(root / 'robot-composed-source-provenance.json'),
        native_composed_smoke=read_json(root / 'native-composed-smoke.json'),
        memory_backend_preparation=read_json(root / 'ramulator-build-provenance.json'),
        timing_order=read_json(root / 'creation/matrix-ordering.json'),
        exporter_sha256=sha256(Path(__file__)),
        limits=[
            'Logical creation means per-sample preparation start with preallocated payloads; not physical capture or initial allocation.',
            'Functional-copy mode excludes scoped copy/fill costs; its timing model is reported separately.',
            'Host timestamps use the logical coupling clock; host computation while gem5 is paused has zero simulation-time cost in both modes.',
            'Actual robot recovery is KVM correctness, not timing-model latency evidence.',
            'The actual robot uses 0.5 ms coupling/physics and 1 kHz joint feedback; the synthetic timing diagnostic uses 2 ms coupling and 250 Hz feedback.',
            'No numerical regression allowance, sustained-capacity claim or full per-flow independence is established.',
            'Guest RSS and thread counts under robot sensor traffic are not measured by the native smoke test.',
            'Buffered tracing adds guest work and shared locking; the callback marker follows a lifecycle guard. Observer overhead has not been isolated.',
            'Robot startup observations use single KVM attempts; the newly compiled candidate also contains current feedback diagnostic logging absent from the retained executable.'])
    write_json(output / 'summary.json', summary)
    fields = ['stage', 'case', 'status', 'mode', 'stream', 'published', 'received',
              'missed_source_releases', 'missing_at_drain', 'duplicates', 'reordered',
              'creation_p99_ms', 'creation_max_ms', 'creation_records', 'preparation_max_ms',
              'publish_to_callback_p99_ms', 'scheduled_age_p99_ms', 'publish_call_max_ms',
              'receipt_window_body_MB_s', 'receipt_window_earlier_publications',
              'receipt_window_source_publications', 'source_cohort_receipt_window_MB_s', 'errors']
    with (output / 'metrics.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for stage, result in suites.items():
            for row in result['attempts']:
                base = dict(stage=stage, case=row['case']['name'], status=row['status'],
                    mode=row.get('config', {}).get('middleware_copy_mode'), errors='; '.join(row['errors']))
                if not row.get('metrics'):
                    writer.writerow(base)
                    continue
                for stream, metric in row['metrics'].items():
                    if stream == 'stereo_pair':
                        continue
                    writer.writerow(dict(base, stream=stream, **{key: metric[key] for key in (
                        'published', 'received', 'missed_source_releases', 'missing_at_drain',
                        'duplicates', 'reordered')},
                        creation_p99_ms=metric['creation_to_callback']['p99_ms'],
                        creation_max_ms=metric['creation_to_callback']['max_ms'],
                        creation_records=metric['creation_to_callback']['count'],
                        preparation_max_ms=metric['preparation']['max_ms'],
                        publish_to_callback_p99_ms=metric['latency']['p99_ms'],
                        scheduled_age_p99_ms=metric['age']['p99_ms'],
                        publish_call_max_ms=metric['publish_call']['max_ms'],
                        receipt_window_body_MB_s=metric['observation']['body_MB_per_observation_second'],
                        receipt_window_earlier_publications=metric['observation']['receipt_publication_cohorts']['before_source_window'],
                        receipt_window_source_publications=metric['observation']['receipt_publication_cohorts']['source_window'],
                        source_cohort_receipt_window_MB_s=metric['observation']['same_source_window_body_MB_per_observation_second']))
    budget = summary['budget'] or {}
    budget_note = (f"All owned simulations were stopped and checked at {budget['verified_stopped_utc']}. "
                   f"Elapsed round time: {budget['elapsed_seconds']:.1f} / {budget['cap_seconds']} seconds."
                   if budget.get('no_owned_simulation_containers_running') else
                   'The round is still active at this export.')
    lines = ['# Creation timestamps and two-core robot recovery: round 2', '',
        '[Interactive report](report.html), [full evidence](summary.json), [per-stream CSV](metrics.csv).', '',
        'The round has one absolute two-hour host wall-time cap, including preparation and recovery. '
        'The first round remains separate. All cases use fresh COW guests; base images are unchanged.', '',
        budget_note, '',
        '## Actual robot recovery', '',
        'The retained guest rejects newer trace manifest fields. The recovery adapter explicitly disables '
        'tracing and removes only its three unsupported fields, preserving transport and application settings. '
        'The retained three-process application then boots but does not reach readiness within 600 seconds.', '',
        'The opt-in `composed:=true` candidate puts the three robot nodes in one process/context, with '
        'one executor for effort control and another for perception/planning. Physics, gains, rates and '
        'algorithm source are preserved. It catches and retains the ball on the fixed two-core geometry. '
        'The wrapper subsequently fails importing the newer tracing postprocessor; offline recovery '
        'uses the saved score, telemetry and statistics without rerunning or replacing them.', '']
    for robot in robots:
        outcome = robot['result'] or ('; '.join(robot['errors']) if robot['errors'] else 'No scored result')
        lines.append('- ' + robot['name'] + ': ' + str(outcome))
    lines += ['', 'The successful catch launches at 0.206 s and scores at 1.706 s, with 0.5315 s '
        'inside the cup. Its first effort receipt is 0.0805 s; the failed retained three-process '
        'attempt first delivers effort at 0.5405 s. These are KVM diagnostic observations, not '
        'TimingSimpleCPU performance claims. The native composed smoke test reports 13 threads '
        'and about 31 MiB RSS without sensor traffic; guest resource qualification remains separate.', '',
        '## Creation boundary and timing comparison', '',
        '`creation_ns` is recorded before logical per-sample preparation. Payloads are preallocated '
        'and reused, so initial allocation and physical sensor capture remain outside that timestamp. '
        'Creation-to-callback includes preparation, publication/serialization, bridge transport, '
        'reception/deserialization and the wait for callback execution. The report also retains '
        'publish-entry latency, scheduled-release age and missing source releases. Historical creation '
        'timestamps are never invented.', '',
        'Guest timestamps use m5 simulation time. Host timestamps use the logical clock at completed '
        'coupling boundaries; host computation while gem5 is paused consumes zero simulation time '
        'in both modes. Timestamp consistency therefore does not calibrate physical host computation '
        'or offload execution costs.', '',
        'The callback timestamp follows a lifecycle guard. Buffered recording, locking and clock '
        'queries add modeled guest work; their observer effect has not been isolated. Both arms '
        'use identical instrumentation, but the results remain instrumented diagnostics rather '
        'than an uninstrumented callback-latency guarantee.', '',
        'Both copy modes preload the identical helper and share the same simulator, endpoint, DDS '
        'library, pools, QoS and two-core geometry. Only the helper mode changes. Functional mode '
        'excludes large scoped middleware copy/fill costs; application computation, prefault stores '
        'and remaining middleware stay modeled. An intended physical offload implementation needs '
        'its operation costs represented or calibrated.', '',
        'The KVM matrix verifies full image bodies and timestamp coverage in four cases. Timing uses '
        '0.05 s warmup, 0.2 s source and 0.1 s drain, with one measured 8 MiB sample per direction '
        'under load. Two interleaved unloaded repetitions per mode precede the loaded pair; a third '
        'reference follows if the cap permits. About 50 joint/effort samples means their p99 equals '
        'the observed maximum; these windows do not establish a statistical tail guarantee.', '']
    lines += ['Warmup bulk transfers may finish inside the measured receipt window. The CSV separates '
              'receipts from earlier publications from receipts published in the measured source '
              'window. Fixed-window goodput and finite publication-cohort delivery remain distinct; '
              'neither demonstrates sustained capacity in these short transients.', '']
    for stage, result in suites.items():
        lines += ['### ' + stage.upper(), '']
        for row in result['attempts']:
            lines.append('- ' + row['case']['name'] + ': ' + row['status'] +
                         ('; ' + '; '.join(row['errors']) if row['errors'] else ''))
        lines.append('')
    timing = suites.get('timing', {})
    lines += ['| Mode / load | Joint / effort / clock creation p99 | Left / right creation max |',
              '|---|---:|---:|']
    for row in timing.get('attempts', []):
        if row['status'] != 'complete' or row['errors'] or not row.get('metrics'):
            continue
        metrics = row['metrics']
        values = ' / '.join(f"{metrics[n]['creation_to_callback']['p99_ms']:.3f}" for n in CONTROLS)
        cameras = ' / '.join(f"{metrics[n]['creation_to_callback']['max_ms']:.3f}" for n in ('left_image', 'right_image'))
        lines.append(f"| {row['config']['middleware_copy_mode']} / {row['case']['cell']} r{row['config']['repeat']} | {values} ms | {cameras} ms |")
    extra = summary.get('extra_correctness') or {}
    actual_copy = extra.get('actual_functional_copy', {})
    lines += ['', '## Additional checks within the same cap', '',
              'Timing/Ruby full-byte integrity: ' + extra.get('cache_integrity', {}).get('status', 'not_run') + '.',
              'Burst-phase sensitivity: ' + ('attempted; see JSON' if extra.get('phase_sensitivity') else 'not_run') + '.',
              'Actual composed functional-copy qualification: ' +
              ('pass' if actual_copy.get('qualified') else
               'not_run' if not actual_copy or actual_copy.get('status') == 'not_run' else
               'not qualified') + '.', '',
              '## Remaining scope', '', *['- ' + item for item in summary['limits']], '',
        'Accepted delivery contract: stereo capture pairs, joint feedback, effort commands, '
        'clock and complete replacement trajectories use latest-sample delivery. Benchmark '
        'records are lossless. At recorder overload, record pressure may delay its producing '
        'callback, including control; those waits are a separately reported exception to the '
        'latency target. Unrelated producers still require isolated progress.', '',
        'Per-topic priorities, mixed lossless/latest admission, reserved flow credits and sustained '
        'stress qualification remain outstanding. The composed application is an opt-in execution '
        'candidate, and the synthetic endpoint still groups camera/bulk work on one executor. '
        'The rebuilt Ramulator-enabled functional-copy simulator is separately prepared and '
        'instantiated at tick zero; that preparation alone is not an actual functional-copy robot pass. '
        'Any later actual run and its copy-counter qualification are reported separately above.', '',
        'Raw evidence and frozen selections: `.chimaera-m5/isolation-20261023-r2/`.', '']
    (output / 'README.md').write_text('\n'.join(lines))
    render(root, output / 'report.html', copy_subdir='creation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.root, args.output)


if __name__ == '__main__':
    main()
