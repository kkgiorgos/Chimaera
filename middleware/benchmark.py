#!/usr/bin/env python3
"""Run matched bridge/middleware latency cases with shared native references."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def snapshot(output):
    source = output / 'source'
    source.mkdir()
    (source / 'base-revision.txt').write_bytes(subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT))
    (source / 'changes.patch').write_bytes(subprocess.check_output(
        ['git', 'diff', 'HEAD', '--binary'], cwd=ROOT))
    files = subprocess.check_output(
        ['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    archive = output / 'source.tar.gz'
    with tarfile.open(archive, 'x:gz') as bundle:
        bundle.add(source, arcname='source')
        for filename in files:
            if filename:
                bundle.add(ROOT / filename, arcname='source/' + filename)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--physical-host', required=True, help='Physical machine recorded in the report')
    for name in ('runtime', 'bundle', 'gem5-root', 'image', 'kernel', 'dma-module'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cpu-type', choices=('kvm', 'timing'), default='timing')
    parser.add_argument('--duration', type=float, default=2.)
    parser.add_argument('--warmup', type=float, default=.25)
    parser.add_argument('--drain', type=float, default=.75)
    parser.add_argument('--timeout', type=int, default=7200)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    common = dict(duration=args.duration, warmup=args.warmup, drain=args.drain,
                  timeout=args.timeout, physics_step_ns=1000000, interval_us=2000,
                  poll_us=1000, cpu_type=args.cpu_type, transport_backend='dma',
                  dma_module=str(args.dma_module.resolve()), dds_shm_bytes=16777216,
                  dds_tuning='large_shm_only', dds_max_message_bytes=1048576,
                  delivery_policy='lossless', verify_payload=True)
    cases = []
    for workload in ('synthetic', 'ball_catching'):
        config = dict(common, workload=workload)
        if workload == 'ball_catching':
            config['ball_catching'] = dict(camera_width=480, camera_height=360,
                camera_hz=30., joint_hz=250., control_hz=250., image_age_periods=2.)
        for mode in ('native_free', 'native_stepped'):
            cases.append(dict(name=workload + '-' + mode, variant='off',
                              config=dict(config, mode=mode)))
        for middleware in ('bridge', 'fastdds'):
            cases.append(dict(name=workload + '-' + middleware, variant='off',
                config=dict(config, mode='chimaera', middleware_transport=middleware)))
    case_file = output / 'cases.json'
    case_file.write_text(json.dumps(cases, indent=2) + '\n')
    command = [sys.executable, str(ROOT / 'profiling/chimaera_rewrite/dds_large_messages.py'),
               str(ROOT / 'profiling/chimaera_latency/run_cases.py'),
               str(case_file), str(output / 'attempts'), '--source-snapshot', str(snapshot(output))]
    for name in ('runtime', 'bundle', 'gem5_root', 'image', 'kernel'):
        command += ['--' + name.replace('_', '-'), str(getattr(args, name).resolve())]
    result = subprocess.run(command)
    if result.returncode:
        return result.returncode
    progress = json.loads((output / 'attempts/progress.json').read_text())
    complete = len(progress) == len(cases) and all(
        case['result'] == 'complete' for case in progress)
    export = [sys.executable, str(ROOT / 'profiling/chimaera_rewrite/collect_integrated.py'),
              str(output / 'attempts'), '--output', str(output / 'evidence'),
              '--physical-host', args.physical_host]
    if not complete:
        export.append('--history-only')
    subprocess.run(export, check=True)
    subprocess.run([sys.executable, '-m', 'chimaera_ros.latency', 'report',
                    str(output / 'attempts')], check=True)
    failures = []
    for summary in sorted((output / 'attempts').glob('*/summary.json')):
        data = json.loads(summary.read_text())
        for name, metric in (data.get('streams') or data['directions']).items():
            if (not metric['published'] or metric['missing_at_drain'] or
                    metric['duplicates'] or metric['reordered']):
                failures.append(dict(case=summary.parent.name, stream=name,
                    published=metric['published'], received=metric['received'],
                    missing_at_drain=metric['missing_at_drain'],
                    duplicates=metric['duplicates'], reordered=metric['reordered']))
    (output / 'delivery-check.json').write_text(json.dumps(dict(
        all_cases_complete=complete, failures=failures), indent=2) + '\n')
    return 0 if complete and not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
