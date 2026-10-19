"""Synthetic, directional, simulation-time ROS latency experiments."""
import argparse
import csv
import datetime
import hashlib
import html
import itertools
import json
import math
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import sys
import tarfile
import time
import uuid
from xml.sax.saxutils import escape

from .runner import prefix, stage
from .manifest import load

MODES = ('native_free', 'native_stepped', 'chimaera')
UNSET = 2**64 - 1
GUEST_ROOT = '/opt/chimaera/latency'


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def parameters(path, values):
    # JSON scalar syntax is a subset of YAML; no shell substitution is involved.
    path.write_text('/**:\n  ros__parameters:\n' + ''.join(
        f'    {key}: {json.dumps(value)}\n' for key, value in values.items()))


def routes(reliability='reliable', depth=128):
    qos = dict(history='keep_last', depth=depth, reliability=reliability, durability='volatile')
    topics = [dict(host_topic=f'/latency/{direction}/host',
                 guest_topic=f'/latency/{direction}/guest',
                 type='std_msgs/msg/UInt8MultiArray', direction=way, qos=qos)
            for direction, way in [('h2g', 'host_to_guest'), ('g2h', 'guest_to_host')]] + [
        dict(host_topic='/latency/guest_events', guest_topic='/latency/guest_events',
             type='std_msgs/msg/UInt64MultiArray', direction='guest_to_host',
             qos=dict(qos, reliability='reliable'))]
    return dict(version=1, topics=topics)


def world(clock_file, physics_step_ns):
    return f'''<?xml version="1.0"?>
<sdf version="1.8"><world name="latency_world">
  <physics name="physics" type="ignored">
    <max_step_size>{physics_step_ns / 1e9:.9f}</max_step_size>
    <real_time_factor>1</real_time_factor>
  </physics>
  <plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
  <plugin filename="libchimaera_latency_clock.so" name="chimaera_latency::PhysicsClock">
    <clock_file>{escape(str(clock_file))}</clock_file>
  </plugin>
</world></sdf>
'''


def validate(config):
    for key in ('rate_hz', 'duration', 'warmup', 'drain'):
        if not math.isfinite(config[key]):
            raise ValueError(f'{key} must be finite')
    if not 0 < config['rate_hz'] <= 10000 or config['duration'] <= 0 or config['warmup'] < 0:
        raise ValueError('Require 0 < rate_hz <= 10000, duration > 0, warmup >= 0')
    if config['drain'] <= 0 or config['duration'] + config['warmup'] + config['drain'] > 3600:
        raise ValueError('Require drain > 0 and at most one simulated hour per case')
    if (config['duration'] + config['warmup']) * config['rate_hz'] > 1000000:
        raise ValueError('At most one million source messages per endpoint')
    if not 0 <= config['payload_bytes'] <= 16 * 1024 * 1024:
        raise ValueError('payload_bytes must be between 0 and 16 MiB')
    if not 0 < config['poll_us'] < config['interval_us'] <= 3600000000:
        raise ValueError('Require 0 < poll_us < interval_us <= one hour')
    if config['physics_step_ns'] < 1 or config['interval_us'] * 1000 % config['physics_step_ns']:
        raise ValueError('Synchronization interval must contain integral physics steps')
    if not 1 <= config['depth'] <= 128 or config['timeout'] <= 0:
        raise ValueError('Require 1 <= depth <= 128 and timeout > 0')


def stage_guest(output, package, bundle=None):
    output = output.resolve()
    # Build the same three routes once. Run-specific routes/QoS are injected at
    # boot into a fixed guest path, so sweeps need no disk-image redeployment.
    if output.exists():
        raise ValueError('Stage output must not exist')
    spec_dir = output.parent / (output.name + '-config')
    spec_dir.mkdir()
    write_json(spec_dir / 'routes.json', routes())
    spec = dict(version=1, routes=str(spec_dir / 'routes.json'),
        host=dict(domain_id=41, setup=['/opt/ros/humble/setup.bash'], processes=[]),
        guest=dict(domain_id=42,
            setup=['/opt/ros/humble/setup.bash', GUEST_ROOT + '/app/share/chimaera_ros_bridge/local_setup.bash'],
            processes=[dict(name='latency_endpoint', command=[
                'ros2', 'run', 'chimaera_ros_bridge', 'latency_endpoint', '--ros-args',
                '--params-file', '/tmp/chimaera-latency.yaml'])]),
        bridge=dict(m5ops='address', wait_for_application=True,
                    simulation_time_from_readfile=True, max_serialized_bytes=16777344),
        deploy=dict(guest_root=GUEST_ROOT,
                    install_trees=[dict(source=str(package), destination='app')]))
    write_json(spec_dir / 'session.json', spec)
    result = stage(load(spec_dir / 'session.json'), output, package)
    # Staged session can reference the per-case route file injected at boot.
    staged = json.loads((result / 'session.json').read_text())
    staged['routes'] = '/tmp/chimaera-latency-routes.json'
    write_json(result / 'session.json', staged)
    if bundle:
        def archive_entry(member):
            # Guest RTCs commonly predate the build host. Avoid one future-time
            # warning per file on the serial console during every boot.
            member.mtime = 0
            return member
        with tarfile.open(bundle, 'x:gz') as archive:
            archive.add(result, arcname=GUEST_ROOT.lstrip('/'), filter=archive_entry)
    print(result)


def run_case(output, config, package, gem5_root, image, kernel, bundle=None):
    validate(config)
    if config['mode'] == 'chimaera':
        if not os.access('/dev/kvm', os.R_OK | os.W_OK):
            raise ValueError('KVM mode requires a readable/writable /dev/kvm')
        for artifact in (image, kernel, gem5_root / 'build/X86/gem5.opt'):
            if not artifact.is_file():
                raise ValueError('Missing simulation artifact: ' + str(artifact))
        if bundle and not bundle.is_file():
            raise ValueError('Missing guest bundle: ' + str(bundle))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = dict(config, schema_version=1, status='running', run_id=str(uuid.uuid4()),
                  created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  hostname=platform.node(), platform=platform.platform(),
                  rmw=os.environ.get('RMW_IMPLEMENTATION', 'ROS default'),
                  cpu='kvm' if config['mode'] == 'chimaera' else 'native',
                  clock_contract='gem5 elapsed time; host callbacks use completed gem5 boundary'
                    if config['mode'] == 'chimaera' else 'Gazebo PreUpdate simulation time',
                  packet_header_bytes=40, telemetry='buffered until measurement and drain finish')
    config['image'] = str(image) if config['mode'] == 'chimaera' else None
    config['guest_bundle'] = str(bundle) if bundle else None
    artifacts = [package / 'lib/chimaera_ros_bridge' / name for name in ('latency_endpoint', 'latency_driver')]
    artifacts.append(package / 'lib/libchimaera_latency_clock.so')
    if bundle:
        artifacts.append(bundle)
    config['artifact_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}
    try:
        config['git_commit'] = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
        config['git_dirty'] = bool(subprocess.check_output(
            ['git', 'status', '--porcelain'], stderr=subprocess.DEVNULL, text=True).strip())
    except subprocess.CalledProcessError:
        config['git_commit'] = None
    manifest = output / 'run.json'
    write_json(manifest, config)
    physics = output / 'physics-clock.bin'
    logical = output / 'logical-clock.bin' if config['mode'] == 'chimaera' else physics
    for path in {physics, logical}:
        with path.open('xb') as handle:
            handle.write(struct.pack('<QQ', 0x434c42434c4f434b, UNSET))
    (output / 'world.sdf').write_text(world(physics, config['physics_step_ns']))
    write_json(output / 'routes.json', routes(config['reliability'], config['depth']))
    common = dict(payload_bytes=config['payload_bytes'], rate_hz=float(config['rate_hz']),
                  warmup_ns=round(config['warmup'] * 1e9),
                  duration_ns=round(config['duration'] * 1e9), drain_ns=round(config['drain'] * 1e9),
                  reliability=config['reliability'], depth=config['depth'])
    parameters(output / 'host.yaml', dict(common, side='host',
        bridged=config['mode'] == 'chimaera', clock_kind='shared', clock_file=str(logical),
        physics_clock_file=str(physics), ready_file=str(output / 'host-ready'),
        done_file=str(output / 'complete'), output_file=str(output / 'events.csv')))
    parameters(output / 'guest.yaml', dict(common, side='guest',
        bridged=config['mode'] == 'chimaera',
        clock_kind='gem5' if config['mode'] == 'chimaera' else 'shared',
        gem5_clock_library=GUEST_ROOT + '/app/lib/libchimaera_gem5_clock_addr.so',
        clock_file=str(physics) if config['mode'] != 'chimaera' else '',
        physics_clock_file=str(physics) if config['mode'] != 'chimaera' else '',
        ready_file=str(output / 'guest-ready') if config['mode'] != 'chimaera' else ''))
    parameters(output / 'driver.yaml', dict(mode=config['mode'],
        interval_us=config['interval_us'], poll_us=config['poll_us'],
        physics_step_ns=config['physics_step_ns'], timeout_s=config['timeout'],
        max_sim_ns=round((config['warmup'] + config['duration'] + config['drain'] + 30) * 1e9),
        clock_file=str(logical), physics_clock_file=str(physics),
        host_ready=str(output / 'host-ready'), guest_ready=str(output / 'guest-ready'),
        done_file=str(output / 'complete'), routes=str(output / 'routes.json'),
        max_serialized_bytes=max(65536, config['payload_bytes'] + 128)))
    executable = package / 'lib/chimaera_ros_bridge'
    commands = [('gazebo', ['ign', 'gazebo', '-s', str(output / 'world.sdf')]),
                ('host', [str(executable / 'latency_endpoint'), '--ros-args', '--params-file', str(output / 'host.yaml')])]
    if config['mode'] == 'chimaera':
        module = package / 'share/chimaera_ros_bridge/config'
        commands.append(('gem5', [str(gem5_root / 'build/X86/gem5.opt'),
            '--outdir=' + str(output / 'gem5'), '-p', str(module), str(module / 'gem5_latency.py'),
            '--gem5-root', str(gem5_root), '--image', str(image), '--kernel', str(kernel),
            '--guest-command', GUEST_ROOT + '/guest_start', '--managed-shutdown',
            '--guest-file', '/tmp/chimaera-latency.yaml=' + str(output / 'guest.yaml'),
            '--guest-file', '/tmp/chimaera-latency-routes.json=' + str(output / 'routes.json')]))
        if bundle:
            commands[-1][1].extend(['--guest-bundle', str(bundle)])
    else:
        commands.append(('guest', [str(executable / 'latency_endpoint'), '--ros-args', '--params-file', str(output / 'guest.yaml')]))
    commands.append(('driver', [str(executable / 'latency_driver'), '--ros-args', '--params-file', str(output / 'driver.yaml')]))
    write_json(output / 'commands.json', commands)
    env = dict(os.environ, ROS_DOMAIN_ID='41', ROS_LOCALHOST_ONLY='1',
               ROS_LOG_DIR=str(output / 'ros-logs'),
               IGN_PARTITION='chimaera-latency-' + config['run_id'],
               IGN_GAZEBO_SYSTEM_PLUGIN_PATH=str(package / 'lib') + ':' + os.environ.get('IGN_GAZEBO_SYSTEM_PLUGIN_PATH', ''),
               IGN_LOG_PATH=str(output / 'ign-logs'))
    processes, logs = [], []
    started = time.monotonic()
    try:
        for name, command in commands:
            log = (output / (name + '.log')).open('w')
            logs.append(log)
            processes.append((name, subprocess.Popen(command, env=env, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)))
        driver = processes[-1][1]
        while driver.poll() is None:
            if time.monotonic() - started > config['timeout'] + 15:
                raise RuntimeError('Run exceeded wall timeout')
            for name, process in processes[:-1]:
                if process.poll() is not None:
                    # A clean gem5 exit is expected immediately after driver's QUIT.
                    if name == 'gem5' and process.returncode == 0 and (output / 'complete').exists():
                        continue
                    raise RuntimeError(f'{name} exited ({process.returncode}); see {name}.log')
            time.sleep(.05)
        if driver.returncode != 0 or not (output / 'complete').exists():
            raise RuntimeError('Driver failed or guest export incomplete; see driver.log')
        config['status'] = 'complete'
    except BaseException as error:
        config['status'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
        config['error'] = str(error)
        raise
    finally:
        for _, process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while any(process.poll() is None for _, process in processes) and time.monotonic() < deadline:
            time.sleep(.05)
        for _, process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
        for log in logs:
            log.close()
        config['wall_seconds'] = time.monotonic() - started
        write_json(manifest, config)
    try:
        write_json(output / 'summary.json', summarize(output))
    except ValueError as error:
        config.update(status='failed', error=str(error))
        write_json(manifest, config)
        raise
    print(f"{config['mode']}: {output}", flush=True)


def statistics(values):
    if not values:
        return dict(count=0, mean_ms=None, p50_ms=None, p95_ms=None, p99_ms=None, max_ms=None)
    values = sorted(values)
    quantile = lambda fraction: values[max(0, math.ceil(len(values) * fraction) - 1)] / 1e6
    return dict(count=len(values), mean_ms=sum(values) / len(values) / 1e6,
                p50_ms=quantile(.5), p95_ms=quantile(.95), p99_ms=quantile(.99), max_ms=values[-1] / 1e6)


def summarize(directory):
    config = json.loads((directory / 'run.json').read_text())
    if config['status'] != 'complete':
        raise ValueError('Incomplete run has no latency summary')
    sends, receipts = {}, {'host': [], 'guest': []}
    with (directory / 'events.csv').open() as handle:
        for row in csv.DictReader(handle):
            if row['side'] not in receipts or row['event'] not in ('send', 'receive'):
                raise ValueError('Invalid event identity')
            event = {key: int(value) for key, value in row.items() if key not in ('side', 'event')}
            if any(value < 0 or value > UNSET for value in event.values()):
                raise ValueError('Invalid unsigned event timestamp')
            key = row['side'], event['sequence']
            if row['event'] == 'send':
                if key in sends:
                    raise ValueError('Duplicate source record')
                sends[key] = event
            else:
                receipts[row['side']].append(event)
    start = round(config['warmup'] * 1e9)
    stop = start + round(config['duration'] * 1e9)
    result = dict(config=config, directions={})
    for source, destination, direction in [('host', 'guest', 'host_to_guest'), ('guest', 'host', 'guest_to_host')]:
        selected = {seq: event for (side, seq), event in sends.items()
                    if side == source and start <= event['publish_ns'] < stop}
        received, latencies, duplicates, reordered, largest = set(), [], 0, 0, -1
        for event in receipts[destination]:
            original = sends.get((source, event['sequence']))
            if original is None or any(original[key] != event[key] for key in ('publish_ns', 'scheduled_ns', 'payload_bytes')):
                raise ValueError('Receipt does not match an exported source event')
            if event['callback_ns'] < event['publish_ns']:
                raise ValueError('Negative simulation latency')
            seq = event['sequence']
            if seq not in selected:
                continue
            if seq in received:
                duplicates += 1
                continue
            if seq < largest:
                reordered += 1
            largest = max(largest, seq)
            received.add(seq)
            latencies.append(event['callback_ns'] - event['publish_ns'])
        release_lags = [event['publish_ns'] - event['scheduled_ns'] for event in selected.values()]
        if any(lag < 0 for lag in release_lags):
            raise ValueError('Message published before its scheduled release')
        result['directions'][direction] = dict(latency=statistics(latencies),
            release_lag=statistics(release_lags), published=len(selected), received=len(received),
            missing_at_drain=len(selected) - len(received), duplicates=duplicates, reordered=reordered,
            delivery_fraction=len(received) / len(selected) if selected else None,
            actual_source_hz=len(selected) / config['duration'],
            p99_tail_samples=len(latencies) / 100,
            native_clock_step_ms=config['physics_step_ns'] / 1e6 if config['mode'] != 'chimaera' else None)
    return result


def make_report(root):
    runs, excluded = [], []
    paths = sorted(root.rglob('run.json'))
    for manifest in paths:
        config = json.loads(manifest.read_text())
        if config.get('status') != 'complete':
            excluded.append(dict(path=str(manifest.parent), status=config.get('status'), error=config.get('error')))
            continue
        runs.append(summarize(manifest.parent))
    comparisons = []
    # Compare independent run distributions. A quantile difference is never
    # labeled a per-message distribution of extra delay.
    match = ('payload_bytes', 'rate_hz', 'reliability', 'depth', 'physics_step_ns', 'duration', 'warmup', 'drain', 'repeat')
    for measured in runs:
        if measured['config']['mode'] != 'chimaera':
            continue
        for baseline in runs:
            if baseline['config']['mode'] == 'chimaera' or any(measured['config'][k] != baseline['config'][k] for k in match):
                continue
            if any(measured['config'].get(key) != baseline['config'].get(key) for key in ('rmw', 'hostname')):
                continue
            def fingerprints(config):
                return {Path(path).name: digest for path, digest in config.get('artifact_sha256', {}).items()
                        if Path(path).name in ('latency_endpoint', 'libchimaera_latency_clock.so')}
            if fingerprints(measured['config']) != fingerprints(baseline['config']):
                continue
            if baseline['config']['mode'] == 'native_stepped' and baseline['config']['interval_us'] != measured['config']['interval_us']:
                continue
            for direction in measured['directions']:
                lhs = measured['directions'][direction]['latency']
                rhs = baseline['directions'][direction]['latency']
                comparisons.append(dict(run_id=measured['config']['run_id'],
                    baseline_id=baseline['config']['run_id'], baseline=baseline['config']['mode'], direction=direction,
                    differences_ms={key: lhs[key] - rhs[key] if lhs[key] is not None and rhs[key] is not None else None
                                    for key in ('mean_ms', 'p50_ms', 'p95_ms', 'p99_ms')}))
    report = dict(runs=runs, excluded=excluded, comparisons=comparisons,
        interpretation='Simulation-time publish-to-callback delay, including DDS, queues, polling, synchronization and guest scheduling. Missing messages are censored at the drain boundary. Native zero means no physics clock tick elapsed; native clock resolution is the physics step. Differences compare independent distributions, not paired messages. KVM results do not model detailed CPU execution.')
    write_json(root / 'latency-report.json', report)
    fields = ['mode', 'repeat', 'payload_bytes', 'rate_hz', 'interval_us', 'poll_us', 'direction',
              'published', 'received', 'missing_at_drain', 'mean_ms', 'p50_ms', 'p95_ms', 'p99_ms', 'max_ms', 'release_lag_mean_ms']
    with (root / 'latency-report.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        for run in runs:
            for direction, metrics in run['directions'].items():
                writer.writerow(dict({key: run['config'][key] for key in fields[:6]}, direction=direction,
                    **{key: metrics[key] for key in ('published', 'received', 'missing_at_drain')},
                    **{key: metrics['latency'][key] for key in fields[10:15]},
                    release_lag_mean_ms=metrics['release_lag']['mean_ms']))
    rows = []
    number = lambda value: '—' if value is None else f'{value:.3f}'
    for run in runs:
        config = run['config']
        for direction, metrics in run['directions'].items():
            latency = metrics['latency']
            timing = '—' if config['mode'] == 'native_free' else (
                f"{config['interval_us'] / 1000:g} / —" if config['mode'] == 'native_stepped' else
                f"{config['interval_us'] / 1000:g} / {config['poll_us'] / 1000:g}")
            cells = [f"{config['mode']} · {config['repeat']}", direction.replace('_to_', ' → '), str(config['payload_bytes']), str(config['rate_hz']),
                     timing,
                     f"{metrics['received']} / {metrics['published']}",
                     *[number(latency[key]) for key in ('p50_ms', 'p95_ms', 'p99_ms')],
                     number(metrics['release_lag']['mean_ms'])]
            rows.append('<tr>' + ''.join('<td>' + html.escape(cell) + '</td>' for cell in cells) + '</tr>')
    differences = []
    by_id = {run['config']['run_id']: run['config'] for run in runs}
    for comparison in comparisons:
        config = by_id[comparison['run_id']]
        cells = [f"{config['payload_bytes']} B · {config['rate_hz']:g} Hz · "
                 f"{config['interval_us'] / 1000:g}/{config['poll_us'] / 1000:g} ms · repeat {config['repeat']}",
                 comparison['direction'].replace('_to_', ' → '), comparison['baseline'],
                 *[number(comparison['differences_ms'][key]) for key in ('mean_ms', 'p95_ms', 'p99_ms')]]
        differences.append('<tr>' + ''.join('<td>' + html.escape(cell) + '</td>' for cell in cells) + '</tr>')
    headers = ['Mode', 'Direction', 'Body bytes', 'Requested Hz', 'Sync / poll ms', 'Received / sent', 'p50 ms', 'p95 ms', 'p99 ms', 'Release lag ms']
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chimaera message latency</title><style>body{font:15px system-ui,sans-serif;margin:40px auto;padding:0 24px;max-width:1400px;color:#182735;background:#f6f8fa}h1{font-size:30px}p{max-width:1000px;line-height:1.6}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:right;padding:12px;border-bottom:1px solid #dce2e7}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}th{background:#e6edf3}small{color:#475b6b}.scroll{overflow:auto}</style>
<h1>Message latency in simulation time</h1><p>''' + html.escape(report['interpretation']) + '''</p>
<p><small>Percentiles use nearest rank. Small runs validate the harness; p99 needs enough received samples. Release lag is publish time minus requested release time, reported separately from delivery latency.</small></p><div class="scroll"><table><thead><tr>''' + ''.join('<th>' + title + '</th>' for title in headers) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><p>' + f'{len(runs)} complete runs; {len(excluded)} excluded runs. Full records and native-baseline differences are in latency-report.json.</p></html>'
    if differences:
        difference_table = '<h2>Chimaera minus native baseline</h2><p>Differences of independent run summaries, in simulated milliseconds. Positive values indicate greater delay with Chimaera.</p><div class="scroll"><table><thead><tr>' + ''.join(
            '<th>' + title + '</th>' for title in ['Condition', 'Direction', 'Native control', 'Mean Δ ms', 'p95 Δ ms', 'p99 Δ ms']) + '</tr></thead><tbody>' + ''.join(differences) + '</tbody></table></div>'
        document = document.replace('</html>', difference_table + '</html>')
    (root / 'latency-report.html').write_text(document)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    staging = sub.add_parser('stage', help='Stage the guest runtime without mounting an image')
    staging.add_argument('--output', type=Path, required=True)
    staging.add_argument('--bundle', type=Path, help='Also create an archive for boot-time guest installation')
    reporting = sub.add_parser('report')
    reporting.add_argument('directory', type=Path)
    for action in ('run', 'sweep'):
        command = sub.add_parser(action)
        command.add_argument('--output', type=Path, required=True)
        command.add_argument('--mode' if action == 'run' else '--modes', choices=MODES,
                             nargs=None if action == 'run' else '+', default='native_free' if action == 'run' else list(MODES))
        command.add_argument('--payload-bytes', type=int, nargs=None if action == 'run' else '+', default=64 if action == 'run' else [64, 1024])
        command.add_argument('--rate-hz', type=float, nargs=None if action == 'run' else '+', default=20. if action == 'run' else [20., 100.])
        command.add_argument('--interval-us', type=int, nargs=None if action == 'run' else '+', default=50000 if action == 'run' else [50000, 20000, 10000])
        command.add_argument('--poll-us', type=int, nargs=None if action == 'run' else '+', default=10000 if action == 'run' else [10000, 5000, 1000])
        command.add_argument('--duration', type=float, default=2.)
        command.add_argument('--warmup', type=float, default=.5)
        command.add_argument('--drain', type=float, default=.5)
        command.add_argument('--physics-step-ns', type=int, default=1000000)
        command.add_argument('--reliability', choices=('reliable', 'best_effort'), default='reliable')
        command.add_argument('--depth', type=int, default=128)
        command.add_argument('--timeout', type=int, default=300)
        command.add_argument('--gem5-root', type=Path, default=Path.cwd() / 'gem5')
        command.add_argument('--image', type=Path)
        command.add_argument('--kernel', type=Path)
        command.add_argument('--guest-bundle', type=Path, help='Install a staged archive inside the guest at boot')
        if action == 'sweep':
            command.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    try:
        if args.action == 'report':
            report = make_report(args.directory.resolve())
            print(f"{len(report['runs'])} complete runs; {len(report['excluded'])} excluded")
            return
        package = prefix()
        if args.action == 'stage':
            stage_guest(args.output, package, args.bundle)
            return
        gem5_root = args.gem5_root.resolve()
        image = (args.image or gem5_root / 'resources/x86-ubuntu-22.04-ros-humble.img').resolve()
        kernel = (args.kernel or gem5_root / 'resources/x86-linux-kernel-5.15.180').resolve()
        bundle = args.guest_bundle.resolve() if args.guest_bundle else None
        config = {key: getattr(args, key) for key in ('duration', 'warmup', 'drain', 'physics_step_ns', 'reliability', 'depth', 'timeout')}
        if args.action == 'run':
            config.update({key: getattr(args, key) for key in ('mode', 'payload_bytes', 'rate_hz', 'interval_us', 'poll_us')}, repeat=1)
            run_case(args.output, config, package, gem5_root, image, kernel, bundle)
            make_report(args.output)
        else:
            if args.repeats < 1:
                raise ValueError('repeats must be positive')
            args.output.mkdir(parents=True, exist_ok=False)
            index = 0
            for body, rate, repeat in itertools.product(args.payload_bytes, args.rate_hz, range(1, args.repeats + 1)):
                for mode in args.modes:
                    pairs = [(args.interval_us[0], args.poll_us[0])] if mode == 'native_free' else (
                        [(interval, min(args.poll_us[0], interval - 1)) for interval in args.interval_us]
                        if mode == 'native_stepped' else [(interval, poll) for interval in args.interval_us for poll in args.poll_us if poll < interval])
                    for interval, poll in pairs:
                        index += 1
                        run_case(args.output / f'case_{index:04d}', dict(config, mode=mode, payload_bytes=body,
                            rate_hz=rate, repeat=repeat, interval_us=interval, poll_us=poll), package, gem5_root, image, kernel, bundle)
            make_report(args.output)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
