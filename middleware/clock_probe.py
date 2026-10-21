#!/usr/bin/env python3
"""Optional guest clock diagnostic using address m5 operations (including KVM)."""
import ctypes
import json
import os
from pathlib import Path
import time


def main():
    library = ctypes.CDLL('/opt/chimaera/ball_catching/lib/libchimaera_gem5_clock_addr.so')
    now = library.chimaera_gem5_time_ns
    now.restype = ctypes.c_uint64
    write = library.m5_write_file_addr
    write.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p]
    write.restype = ctypes.c_uint64
    origin = Path('/tmp/chimaera-sim-time-origin.ns')
    while not origin.exists():
        time.sleep(.01)
    origin_ns = int(origin.read_text())
    result = dict(
        clocksource=Path('/sys/devices/system/clocksource/clocksource0/current_clocksource')
        .read_text().strip(),
        available_clocks=Path('/sys/devices/system/clocksource/clocksource0/available_clocksource')
        .read_text().strip(),
        timer_status=Path('/proc/timer_list').read_text(),
        started_ns=now() - origin_ns, samples=[])

    def export():
        result['updated_ns'] = now() - origin_ns
        data = (json.dumps(result, indent=2) + '\n').encode()
        buffer = ctypes.create_string_buffer(data)
        if write(buffer, len(data), 0, b'chimaera-clock-probe.json') != len(data):
            raise RuntimeError('clock probe export failed')

    export()
    for cpu in sorted(os.sched_getaffinity(0)):
        os.sched_setaffinity(0, {cpu})
        for requested_us in (1000, 5000):
            for _ in range(2):
                start = now()
                os_start = time.monotonic_ns()
                time.sleep(requested_us / 1e6)
                os_end = time.monotonic_ns()
                end = now()
                result['samples'].append(dict(cpu=cpu, requested_us=requested_us,
                    os_ns=os_end - os_start, simulation_ns=end - start))
                export()
    result['interrupts'] = Path('/proc/interrupts').read_text()
    export()
    # The guest supervisor treats any child exit as session termination.
    while True:
        time.sleep(60)


if __name__ == '__main__':
    main()
