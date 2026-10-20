#!/usr/bin/env python3
"""Guest clock oracle for an Atomic/detailed CPU experiment; NEVER run under KVM.

Wait for workbegin/CPU switching to finish, then compare Linux wakeups and
monotonic time against authoritative gem5 rpns on every available guest CPU.
The instruction m5 library is deliberately required. Export outside the measured
source window, with at least 200 ms warmup when this probe is enabled.
"""
import ctypes
import json
import os
from pathlib import Path
import re
import time


def main():
    library = ctypes.CDLL('/opt/chimaera/latency/app/lib/libchimaera_gem5_clock.so')
    now = library.chimaera_gem5_time_ns
    now.restype = ctypes.c_uint64
    origin = Path('/tmp/chimaera-sim-time-origin.ns')
    while not origin.exists():
        time.sleep(.01)
    origin_ns = int(origin.read_text().strip())
    result = dict(started_ns=now() - origin_ns,
                  clocksource=Path('/sys/devices/system/clocksource/clocksource0/'
                                  'current_clocksource').read_text().strip(),
                  monotonic_resolution=time.get_clock_info('monotonic').resolution,
                  timer_status=[line.strip() for line in Path('/proc/timer_list').read_text().splitlines()
                                if re.search('resolution|hres_active', line)], samples=[])
    for cpu in sorted(os.sched_getaffinity(0)):
        os.sched_setaffinity(0, {cpu})
        for requested_us in (100, 500, 1000):
            for _ in range(20):
                simulated_start = now()
                os_start = time.monotonic_ns()
                time.sleep(requested_us / 1e6)
                os_end = time.monotonic_ns()
                simulated_end = now()
                result['samples'].append(dict(cpu=cpu, requested_us=requested_us,
                                              os_ns=os_end - os_start,
                                              simulation_ns=simulated_end - simulated_start))
    result['finished_ns'] = now() - origin_ns
    data = (json.dumps(result, indent=2) + '\n').encode()
    write = library.m5_write_file
    write.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p]
    write.restype = ctypes.c_uint64
    buffer = ctypes.create_string_buffer(data)
    if write(buffer, len(data), 0, b'chimaera-clock-probe.json') != len(data):
        raise RuntimeError('clock probe export failed')


if __name__ == '__main__':
    main()
