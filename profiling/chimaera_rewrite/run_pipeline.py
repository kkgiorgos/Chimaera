#!/usr/bin/env python3
"""Run run_cases.py with buffered per-process pipeline traces enabled.

Compose with dds_large_messages.py when matching its SHM-only DDS profile.
The guest exports records through m5 only after the source/drain window.
"""
import os
from pathlib import Path
import runpy
import sys

from chimaera_ros import latency


def main():
    original_run = latency.run_case
    original_session = latency.guest_session
    cutoff = 0

    def run_case(output, config, *args, **kwargs):
        nonlocal cutoff
        if config['mode'] == 'chimaera' and config.get('cpu_type') != 'kvm':
            raise ValueError('Pipeline diagnostics are restricted to KVM')
        cutoff = round(sum(config[k] for k in ('warmup', 'duration', 'drain')) * 1e9)
        kwargs['environment'] = dict(kwargs.get('environment') or os.environ,
            CHIMAERA_PIPELINE='1', CHIMAERA_PIPELINE_DIR=str(output.resolve()))
        return original_run(output, config, *args, **kwargs)

    def guest_session(*args, **kwargs):
        session = original_session(*args, **kwargs)
        session['guest'].setdefault('env', {}).update(CHIMAERA_PIPELINE='1',
            CHIMAERA_PIPELINE_CUTOFF_NS=str(cutoff))
        return session

    latency.run_case = run_case
    latency.guest_session = guest_session
    runner = Path(__file__).resolve().parents[1] / 'chimaera_latency/run_cases.py'
    sys.argv[0] = str(runner)
    runpy.run_path(str(runner), run_name='__main__')


if __name__ == '__main__':
    main()
