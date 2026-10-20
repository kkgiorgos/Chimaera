#!/usr/bin/env python3
"""Retain successful and failed DMA attempts; require matched native samples."""
import argparse
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text()) if path.is_file() else None


def collect(root):
    results = []
    for case in sorted(root.glob('dma-*')):
        provenance = read(case / 'provenance.json') if case.is_dir() else None
        if not provenance:
            continue
        native = read(case / 'native.json')
        guest = read(case / 'gem5/chimaera-dma-probe.json')
        origin = read(case / 'gem5/dma_probe_origin.json')
        if provenance.get('result') == 'complete':
            if not native or not guest or native['integrity'] != 'pass' or guest['integrity'] != 'pass':
                raise ValueError(f'{case.name}: missing verified native/guest result')
            def sizes(value):
                return sorted((sample['bytes'], sample['repeat']) for sample in value['samples'])
            if not guest['samples'] or sizes(guest) != sizes(native):
                raise ValueError(f'{case.name}: unmatched or empty native workload')
            if not all(sample.get('verified') for sample in guest['samples'] + native['samples']):
                raise ValueError(f'{case.name}: payload integrity not verified')
            if guest['reset_reopen'] != 'pass':
                raise ValueError(f'{case.name}: reset/reopen not verified')
            if provenance['cpu'] == 'timing':
                if not origin or origin.get('active_cpus') != ['BaseTimingSimpleCPU'] * 2:
                    raise ValueError(f'{case.name}: detailed CPU provenance missing/mismatched')
        results.append(dict(case=case.name, provenance=provenance, native=native,
                            guest=guest, origin=origin, command=read(case / 'command.json')))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    records = collect(args.root)
    args.output.write_text(json.dumps(records, indent=2, allow_nan=False) + '\n')
    print(f'Retained {len(records)} DMA attempts, including failed/cancelled runs')


if __name__ == '__main__':
    main()
