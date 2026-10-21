#!/usr/bin/env python3
"""Run copy-reduction comparisons with fresh KVM guests only.

Uses run_cases.py's retained commands, hashes and failure records. Set
copy_router_tests in a candidate case to run the router/ownership regression
executable inside that guest before the ordinary endpoint starts. Build and
include dds_router_test in the staged runtime's lib/chimaera_ros_bridge folder.
Invoke through dds_large_messages.py when selecting the SHM-only DDS profile.
"""
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    from chimaera_ros import latency

    if len(sys.argv) < 3:
        raise SystemExit('Usage: run_copy_cases.py CASES OUTPUT [run_cases.py options]')
    cases = json.loads(Path(sys.argv[1]).read_text())
    if not cases or any(case.get('config', {}).get('cpu_type') != 'kvm' or
                        case.get('config', {}).get('mode') != 'chimaera' for case in cases):
        raise ValueError('Copy-reduction validation requires explicit KVM Chimaera cases only')
    original_run, original_session = latency.run_case, latency.guest_session
    active = {}

    def run_case(output, config, package, *args, **kwargs):
        active.clear()
        active.update(config)
        environment = dict(kwargs.get('environment') or os.environ)
        for key, prefix in (('LD_LIBRARY_PATH', package / 'lib'), ('AMENT_PREFIX_PATH', package)):
            environment[key] = str(prefix) + os.pathsep + environment.get(key, '')
        kwargs['environment'] = environment
        try:
            return original_run(output, config, package, *args, **kwargs)
        finally:
            output.mkdir(parents=True, exist_ok=True)
            (output / 'copy-environment.json').write_text(json.dumps({
                key: environment[key] for key in ('LD_LIBRARY_PATH', 'AMENT_PREFIX_PATH')
            }, indent=2) + '\n')

    def session(*args, **kwargs):
        spec = original_session(*args, **kwargs)
        if active.get('copy_router_tests'):
            process = spec['guest']['processes'][0]
            script = ('/opt/chimaera/latency/app/lib/chimaera_ros_bridge/dds_router_test; '
                      'echo CHIMAERA_COPY_ROUTER_TESTS_PASSED; exec "$@"')
            process['command'] = [
                '/bin/bash', '-ec', script,
                'copy-router-tests', *process['command']]
        return spec

    latency.run_case, latency.guest_session = run_case, session
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'chimaera_latency/run_cases.py'),
                   run_name='__main__')


if __name__ == '__main__':
    main()
