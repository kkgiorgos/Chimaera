#!/usr/bin/env python3
"""Observe physical-clock changes on the host without changing the clock plugin."""
import argparse
import csv
import mmap
from pathlib import Path
import struct
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--seconds', type=float, default=30.)
    args = parser.parse_args()
    rows = []
    with (args.case/'physics-clock.bin').open('rb') as handle:
        with mmap.mmap(handle.fileno(), 16, access=mmap.ACCESS_READ) as memory:
            deadline, previous = time.monotonic()+args.seconds, None
            while time.monotonic() < deadline and not (args.case/'complete').exists():
                sim = struct.unpack_from('<Q', memory, 8)[0]
                wall = time.monotonic_ns()
                if sim != previous and sim != 2**64-1:
                    rows.append((sim, wall))
                    previous = sim
                time.sleep(.001)
    with (args.case/'physics-observer.csv').open('w') as handle:
        writer = csv.writer(handle)
        writer.writerow(['sim_ns', 'os_ns'])
        writer.writerows(rows)


if __name__ == '__main__':
    main()
