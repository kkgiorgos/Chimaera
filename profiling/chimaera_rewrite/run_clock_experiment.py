#!/usr/bin/env python3
"""Run the private Atomic-boot clock experiment in the SDK container."""
import base64
import json
from pathlib import Path
import shlex
import sys

from chimaera_ros import latency

sys.path.insert(0, '/profile/profiling/chimaera_latency')
import run_cases


def main():
    root = Path('/rewrite')
    output = sys.argv[1] if len(sys.argv) > 1 else '/rewrite/clock-experiment'
    cases = json.loads((root / 'clock-cases.json').read_text())
    if any(case['config'].get('cpu_type') == 'kvm' for case in cases):
        raise ValueError('instruction clock probe must never run under KVM')
    original = latency.guest_session
    encoded = base64.b64encode((root / 'clock_probe.py').read_bytes()).decode()

    def session(*args, **kwargs):
        spec = original(*args, **kwargs)
        process = spec['guest']['processes'][0]
        process['command'] = [
            '/bin/bash', '-c',
            'printf %s ' + shlex.quote(encoded) + ' | base64 -d > /tmp/chimaera-clock-probe.py\n'
            'python3 /tmp/chimaera-clock-probe.py &\nexec "$@"',
            'clock-probe', *process['command']]
        return spec

    latency.guest_session = session
    sys.argv = ['run_cases.py', '/rewrite/clock-cases.json', output,
                '--runtime', '/rewrite/runtime-clock', '--bundle', '/rewrite/guest.tar.gz']
    run_cases.main()


if __name__ == '__main__':
    main()
