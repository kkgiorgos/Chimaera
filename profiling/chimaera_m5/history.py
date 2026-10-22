#!/usr/bin/env python3
"""Archive historical run metadata as context, never as newly matched acceptance."""
import argparse
from pathlib import Path

from common import read_json, sha256, write_json


def collect(roots, cohort):
    attempts = []
    for root in roots:
        directories = {p.parent for name in ('run.json', 'profile-provenance.json') for p in root.rglob(name)}
        for directory in sorted(directories):
            record = dict(path=str(directory), cohort=cohort, comparison_eligible=False, artifacts={})
            for name in ('run.json', 'summary.json', 'profile-provenance.json', 'clock-origin.json'):
                try:
                    value = read_json(directory / name)
                    if value is not None:
                        record[name] = value
                        record['artifacts'][name] = sha256(directory / name)
                except (OSError, ValueError) as error:
                    record.setdefault('errors', []).append(name + ': ' + str(error))
            config = record.get('run.json', {})
            record['verify_payload_recorded'] = config.get('verify_payload')
            record['verification_interpretation'] = ('unknown: absent from historical metadata' if
                'verify_payload' not in config else 'enabled' if config['verify_payload'] else 'disabled')
            record['status'] = record.get('profile-provenance.json', {}).get('result', config.get('status', 'unknown'))
            record['definition'] = 'Historical finite source cohort; retain original window, geometry, '
            record['definition'] += 'DDS profile, verifier, active CPU and artifact identities. No cross-cohort speedup.'
            attempts.append(record)
    return dict(schema_version=1, cohort=cohort, comparison_eligible=False, attempts=attempts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', type=Path, nargs='+')
    parser.add_argument('--cohort', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite historical context')
    write_json(args.output, collect(args.roots, args.cohort))


if __name__ == '__main__':
    main()
