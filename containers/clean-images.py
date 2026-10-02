#!/usr/bin/env python3
"""Remove obsolete Chimaera image tags, preserving the selected shared image."""

import argparse
import json
import subprocess


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--keep', action='append', help='Tag to retain (repeatable; replaces defaults)')
    cli.add_argument('--apply', action='store_true', help='Remove listed tags; default only previews')
    args = cli.parse_args()
    keep = set(args.keep or ['chimaera:jammy-humble-fortress'])
    result = subprocess.run(['docker', 'image', 'ls', '--format', '{{json .}}'],
                            capture_output=True, text=True, check=True)
    tags = set()
    for line in result.stdout.splitlines():
        image = json.loads(line)
        if (image['Repository'] == 'chimaera' or image['Repository'].startswith('chimaera-')) and image['Tag'] != '<none>':
            tags.add(image['Repository'] + ':' + image['Tag'])
    missing = keep - tags
    if missing:
        cli.error(f'keep tags are missing: {sorted(missing)}')
    obsolete = sorted(tags - keep)
    # Older guest construction left untagged images with no stack label.
    dangling = subprocess.run(['docker', 'image', 'ls', '--filter', 'dangling=true',
                              '--no-trunc', '--format', '{{.ID}}'],
                             capture_output=True, text=True, check=True)
    for image_id in sorted(set(dangling.stdout.splitlines())):
        info = json.loads(subprocess.run(['docker', 'image', 'inspect', image_id],
                                        capture_output=True, text=True, check=True).stdout)[0]
        labels = info['Config'].get('Labels') or {}
        history = subprocess.run(['docker', 'history', '--no-trunc', '--format', '{{.CreatedBy}}', image_id],
                                 capture_output=True, text=True, check=True).stdout
        if labels.get('io.chimaera.stack') or any(marker in history for marker in
                ('/opt/chimaera', '/build-guest-disk.py', '/install-runtime.py')):
            obsolete.append(image_id)
    for tag in obsolete:
        print(('Removing ' if args.apply else 'Would remove ') + tag, flush=True)
        if args.apply:
            # No force: Docker protects images in use by containers.
            subprocess.run(['docker', 'image', 'rm', tag], check=True)
    if not obsolete:
        print('No obsolete Chimaera tags.')


if __name__ == '__main__':
    main()
