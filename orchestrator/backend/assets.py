"""Remove orchestrator caches while retaining every saved experiment's dependencies."""
import json
from pathlib import Path

from common import run


def clean_unused(data, keep_paths=()):
    data = Path(data)
    paths = {str(Path(p).resolve()) for p in keep_paths if p}
    images = set()
    experiments = data / 'experiments'
    # Include archived failed preparations: they may still be needed for recovery.
    for name in ('runner.json', 'ready.json', 'request.json'):
        for manifest in experiments.rglob(name):
            record = json.loads(manifest.read_text())
            if record.get('image'):
                images.add(record['image'])
            provenance = record.get('provenance') or (record.get('calibration') or {}).get('provenance', {})
            images.update(provenance[key] for key in ('sdk_image_id', 'application_image_id') if provenance.get(key))
            paths.update(str(Path(m['source']).resolve()) for m in record.get('mounts', [])
                         if Path(m['source']).is_absolute())
            paths.update(str(Path(record[key]).resolve()) for key in ('disk', 'kernel') if record.get(key))

    containers = run(['docker', 'ps', '-aq'], text=True).split()
    if containers:
        for container in json.loads(run(['docker', 'inspect', *containers], text=True)):
            images.add(container['Image'])
            paths.update(str(Path(m['Source']).resolve()) for m in container.get('Mounts', [])
                         if m.get('Source'))
    # Resolve old tag-based manifests before removing any tags.
    protected = set()
    for image in images:
        if image.startswith('sha256:'):
            protected.add(image)
        else:
            protected.add(run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                              text=True).strip())

    listing = run(['docker', 'image', 'ls', '--no-trunc', '--format', '{{json .}}'], text=True)
    candidates = {}
    present = set()
    for line in listing.splitlines():
        entry = json.loads(line)
        present.add(entry['ID'])
        if entry['Repository'] == 'chimaera' and entry['Tag'].startswith(('sdk-', 'ball-')):
            candidates.setdefault(entry['ID'], set()).add('chimaera:' + entry['Tag'])
    assets = data / 'assets'
    for marker in assets.glob('image-*.json'):
        image = json.loads(marker.read_text())['image_id']
        if image in present:
            candidates.setdefault(image, set())
        else:
            marker.unlink()
    removed_images = 0
    for image, tags in candidates.items():
        if image in protected:
            continue
        # No force: Docker also protects containers outside this orchestrator.
        run(['docker', 'image', 'rm', *(sorted(tags) or [image])], timeout=120)
        (assets / ('image-' + image.removeprefix('sha256:') + '.json')).unlink(missing_ok=True)
        removed_images += 1
    removed_disks = 0
    freed = 0
    for disk in assets.glob('ball-catching-*.img'):
        if disk.is_symlink() or str(disk.resolve()) in paths:
            continue
        freed += disk.stat().st_blocks * 512
        disk.unlink()
        key = disk.stem.removeprefix('ball-catching-')
        (assets / ('disk-provenance-' + key + '.json')).unlink(missing_ok=True)
        removed_disks += 1
    return dict(images=removed_images, disks=removed_disks, disk_bytes=freed)
