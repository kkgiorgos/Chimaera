#!/usr/bin/env python3
"""Retain every integrated attempt and export only complete, matched measurements."""
import argparse
import csv
import json
from pathlib import Path

from summarize import summarize


def read(path):
    return json.loads(path.read_text()) if path.is_file() else None


def collect(roots):
    attempts, complete = [], []
    for root in roots:
        # Failed startup and manually cancelled cases need not have a summary.
        directories = {p.parent for pattern in ('run.json', 'profile-provenance.json')
                       for p in root.rglob(pattern)}
        for directory in sorted(directories):
            metadata = read(directory / 'run.json') or {}
            provenance = read(directory / 'profile-provenance.json') or {}
            summary = read(directory / 'summary.json')
            status = provenance.get('result', metadata.get('status', 'unknown'))
            attempt = dict(case=str(Path(root.name) / directory.relative_to(root)),
                           result=status, metadata=metadata, provenance=provenance,
                           summary=summary,
                           cpu_origin=read(directory / 'gem5/latency_clock_origin.json'))
            for name in ('diagnostic-stop.json', 'stop-reason.json'):
                value = read(directory / name)
                if value is not None:
                    attempt[name] = value
            attempts.append(attempt)
            if status != 'complete' or summary is None:
                continue
            complete.append(dict(path=attempt['case'], variant=root.name,
                                 absolute_path=str(directory / 'summary.json'),
                                 config=summary['config'],
                                 metrics=summary.get('streams') or summary['directions'],
                                 physical_host=provenance.get('host', metadata.get('hostname')),
                                 provenance=provenance))
    return attempts, complete


def export(roots, output, physical_host, history_only=False):
    attempts, complete = collect(roots)
    rows = [] if history_only else summarize(complete)
    output.mkdir(parents=True, exist_ok=True)
    record = dict(physical_host=physical_host,
                  host_identity_note='Host supplied by operator; per-case host is the isolated container hostname.',
                  comparisons_validated=not history_only,
                  attempts=attempts)
    (output / 'attempts.json').write_text(json.dumps(record, indent=2) + '\n')
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (output / 'comparison.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(attempts), len(complete), len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--physical-host', required=True)
    parser.add_argument('--history-only', action='store_true',
                        help='Archive diagnostic history without treating it as matched acceptance data')
    args = parser.parse_args()
    counts = export(args.roots, args.output, args.physical_host, args.history_only)
    print('%d attempts, %d complete measurements, %d matched stream rows' % counts)


if __name__ == '__main__':
    main()
