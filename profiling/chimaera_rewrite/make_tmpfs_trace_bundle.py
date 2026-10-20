#!/usr/bin/env python3
"""Wrap an immutable guest bundle for diagnostic-only RAM tracing and late export.

Use a source window ending before 0.75 s and a drain ending after 0.9 s.
Runtime/model binaries are unchanged. Export uses an existing address-mode m5
utility after the detailed CPU origin file appears and the source window ends.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('m5', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite a diagnostic bundle')
    root = 'opt/chimaera/latency/'
    wrapper = '''exec 2>/dev/shm/chimaera-image-trace.log
(
    while [ ! -f /tmp/chimaera-sim-time-origin.ns ]; do sleep 0.01; done
    sleep 0.75
    /opt/chimaera/latency/trace_m5 --addr=0xffff0000 writefile \\
        /dev/shm/chimaera-image-trace.log chimaera-image-trace.log >/dev/null
) &
'''
    utility = args.m5.read_bytes()
    metadata = dict(diagnostic_only=True,
                    base_bundle_sha256=hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
                    utility_sha256=hashlib.sha256(utility).hexdigest(),
                    export_sleep_seconds=.75, destination='/dev/shm/chimaera-image-trace.log')
    found = False
    with tarfile.open(args.bundle, 'r:gz') as source, tarfile.open(args.output, 'x:gz') as output:
        for member in source:
            data = source.extractfile(member) if member.isfile() else None
            if member.name == root + 'guest_start':
                if data is None:
                    raise ValueError('Guest startup must be a regular file')
                text = data.read().decode()
                first, separator, remainder = text.partition('\n')
                if not first.startswith('#!') or not separator:
                    raise ValueError('Guest startup lacks a shebang')
                value = (first + '\n' + wrapper + remainder).encode()
                member.size = len(value)
                data = io.BytesIO(value)
                found = True
            output.addfile(member, data)
        if not found:
            raise ValueError('Guest startup was not found')
        for name, value, mode in [('trace_m5', utility, 0o755),
                                   ('trace-bundle.json', (json.dumps(metadata, indent=2) + '\n').encode(), 0o644)]:
            member = tarfile.TarInfo(root + name)
            member.size, member.mode = len(value), mode
            output.addfile(member, io.BytesIO(value))
    print(json.dumps(dict(metadata, bundle_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
