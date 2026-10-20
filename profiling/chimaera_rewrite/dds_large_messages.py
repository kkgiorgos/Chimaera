#!/usr/bin/env python3
"""Experiment adapter: run_cases.py with local-only, large-message DDS buffers.

Usage: dds_large_messages.py CASE_RUNNER CASES OUTPUT [case-runner options...]
Only this adapter interprets dds_tuning / dds_max_message_bytes. Core runtime,
guest and model binaries stay unchanged; XML and adapter provenance are retained.
"""
import hashlib
import json
from pathlib import Path
import runpy
import sys


def large_message_profile(segment_bytes, message_bytes):
    if type(segment_bytes) is not int or type(message_bytes) is not int or not (
            65500 <= message_bytes <= segment_bytes // 2 <= 64 * 1024 * 1024):
        raise ValueError('Large DDS messages require at least twice their size in SHM')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <transport_descriptors><transport_descriptor>
    <transport_id>large_image_shm</transport_id><type>SHM</type>
    <maxMessageSize>{message_bytes}</maxMessageSize>
    <segment_size>{segment_bytes}</segment_size>
  </transport_descriptor></transport_descriptors>
  <participant profile_name="large_image_participant" is_default_profile="true">
    <rtps><userTransports><transport_id>large_image_shm</transport_id></userTransports>
      <useBuiltinTransports>false</useBuiltinTransports>
    </rtps>
  </participant>
</profiles>
'''


def main():
    from chimaera_ros import latency
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    runner = Path(sys.argv.pop(1)).resolve()
    adapter_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    original_run = latency.run_case
    original_profile = latency.shared_memory_profile

    def run_case(output, config, *args, **kwargs):
        mode = config.get('dds_tuning', 'legacy')
        maximum = config.get('dds_max_message_bytes', 4194304)
        if mode not in ('legacy', 'large_shm_only'):
            raise ValueError('Unknown DDS experiment profile')
        latency.shared_memory_profile = (original_profile if mode == 'legacy' else
            lambda segment: large_message_profile(segment, maximum))
        try:
            return original_run(output, config, *args, **kwargs)
        finally:
            latency.shared_memory_profile = original_profile
            output.mkdir(parents=True, exist_ok=True)
            (output / 'dds-overlay.json').write_text(json.dumps(dict(
                adapter_sha256=adapter_sha256, tuning=mode, max_message_bytes=maximum,
                core_binaries_unchanged=True, local_participants_only=mode != 'legacy'), indent=2) + '\n')

    latency.run_case = run_case
    sys.argv[0] = str(runner)
    runpy.run_path(str(runner), run_name='__main__')


if __name__ == '__main__':
    main()
