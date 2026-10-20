#!/usr/bin/env python3
"""Run an explicit case list with fresh guest boots and retain every attempt."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import traceback

from chimaera_ros import latency


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--runtime', type=Path, default=Path('/profile/runtime'))
    parser.add_argument('--original-runtime', type=Path, default=Path('/opt/chimaera/latency-runtime'))
    parser.add_argument('--bundle', type=Path, default=Path('/profile/guest.tar.gz'))
    parser.add_argument('--original-bundle', type=Path, default=Path('/opt/chimaera/latency-guest.tar.gz'))
    parser.add_argument('--gem5-root', type=Path, default=Path('/opt/chimaera/gem5'))
    parser.add_argument('--image', type=Path, default=Path('/assets/guest.img'))
    parser.add_argument('--kernel', type=Path, default=Path('/assets/kernel'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = json.loads(args.cases.read_text())
    original_session = latency.guest_session
    results = []
    for case in cases:
        name = case['name']
        variant = case.get('variant', 'trace')
        config = dict(duration=1., warmup=.2, drain=.4, physics_step_ns=1000000,
                      reliability='reliable', depth=128, timeout=600, workload='synthetic',
                      mode='chimaera', payload_bytes=64, rate_hz=97., repeat=1,
                      interval_us=50000, poll_us=1000, cpu_type='kvm', num_cores=2,
                      cpu_clock='3GHz', l1d_size='16KiB', l1i_size='16KiB',
                      l2_size='256KiB', l1_assoc=8, l2_assoc=16)
        config.update(case.get('config', {}))
        runtime = args.original_runtime if variant == 'original' else args.runtime
        bundle = args.original_bundle if variant == 'original' else args.bundle
        output = args.output / name
        enabled = variant == 'trace'

        def session(*values, **kwargs):
            spec = original_session(*values, **kwargs)
            spec['guest'].setdefault('env', {})['CHIMAERA_PROFILE'] = '1' if enabled else '0'
            if case.get('guest_diagnostics'):
                command = spec['guest']['processes'][0]['command']
                diagnostics = '''
echo CHIMAERA_KERNEL_DIAGNOSTICS_BEGIN
uname -a
cat /sys/devices/system/clocksource/clocksource0/current_clocksource
cat /sys/devices/system/clocksource/clocksource0/available_clocksource
awk '/resolution|hres_active|Tick Device|mode:/' /proc/timer_list
python3 - <<'PY'
import collections,json,time
print('MONOTONIC',time.get_clock_info('monotonic'))
for us in (100,500,1000,5000,10000):
    samples=[]
    for _ in range(20):
        start=time.monotonic_ns();time.sleep(us/1e6);samples.append(time.monotonic_ns()-start)
    print('SLEEP_PROBE',json.dumps(dict(requested_us=us,actual_ns=dict(collections.Counter(samples)))))
PY
echo CHIMAERA_KERNEL_DIAGNOSTICS_END
exec "$@"
'''
                spec['guest']['processes'][0]['command'] = [
                    '/bin/bash', '-c', diagnostics, 'profile-preflight', *command]
            return spec

        latency.guest_session = session
        env = dict(os.environ, CHIMAERA_PROFILE='1' if enabled else '0',
                   CHIMAERA_PROFILE_OUTPUT=str(output / 'profile-host.csv'))
        provenance = dict(case=case, time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          host=platform.node(), loadavg=os.getloadavg(),
                          cpu_max=Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
                          memory_max=Path('/sys/fs/cgroup/memory.max').read_text().strip(),
                          fresh_boot=True,
                          gem5_sha256=hashlib.sha256((args.gem5_root/'build/X86/gem5.opt').read_bytes()).hexdigest())
        print('START', name, json.dumps(case), flush=True)
        try:
            latency.run_case(output, config, runtime, args.gem5_root,
                             args.image, args.kernel, bundle=bundle, environment=env)
            provenance['result'] = 'complete'
        except Exception as error:
            traceback.print_exc()
            provenance.update(result='failed', error=str(error))
        output.mkdir(exist_ok=True)
        (output / 'profile-provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
        results.append(dict(name=name, result=provenance['result']))
        (args.output / 'progress.json').write_text(json.dumps(results, indent=2)+'\n')
        print('FINISH', name, provenance['result'], flush=True)
    latency.guest_session = original_session


if __name__ == '__main__':
    main()
