"""Keep the current reusable assets and unfinished experiments; remove old versions."""
import json
import os
import re
import shutil
from pathlib import Path

from common import run


def allocated_bytes(path):
    """Count allocated blocks once per inode; never follow symlinks."""
    seen = set()
    total = 0
    path = Path(path)
    if path.is_symlink() or not path.exists():
        return 0

    def unreadable(error):
        raise error

    for base, directories, files in os.walk(path, followlinks=False, onerror=unreadable):
        for name in files:
            try:
                entry = (Path(base) / name).lstat()
            except FileNotFoundError:
                continue
            inode = (entry.st_dev, entry.st_ino)
            if inode not in seen:
                seen.add(inode)
                total += entry.st_blocks * 512
    return total


def filesystem_usage(data):
    usage = shutil.disk_usage(data)
    return dict(total=usage.total, used=usage.used, free=usage.free)


def asset_inventory(data, keep_paths=()):
    data = Path(data)
    paths = {str(Path(p).resolve()) for p in keep_paths if p}
    images = set()
    current = data / 'assets/current-images.json'
    if current.exists():
        images.update(record['id'] for record in json.loads(current.read_text()).values())
    guest = data / 'assets/current-guest.json'
    if guest.exists():
        paths.update(str(Path(p).resolve()) for p in json.loads(guest.read_text()).values() if p)
    experiments = data / 'experiments'
    # Include archived failed preparations: they may still be needed for recovery.
    for name in ('runner.json', 'ready.json', 'request.json'):
        for manifest in experiments.rglob(name):
            record = json.loads(manifest.read_text())
            if record.get('image'):
                images.add(record['image'])
            provenance = record.get('provenance') or (record.get('calibration') or {}).get('provenance', {})
            images.update(provenance[key] for key in ('sdk_image_id', 'base_image_id', 'application_image_id') if provenance.get(key))
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

    listing = run(['docker', 'image', 'ls', '--all', '--no-trunc', '--format', '{{json .}}'], text=True)
    candidates = {}
    present = set()
    for line in listing.splitlines():
        entry = json.loads(line)
        present.add(entry['ID'])
        if entry['Repository'] == 'chimaera' and entry['Tag'].startswith(('sdk-', 'ball-', 'transfer-', 'orchestrator-')):
            candidates.setdefault(entry['ID'], set()).add('chimaera:' + entry['Tag'])
    assets = data / 'assets'
    for marker in assets.glob('image-*.json'):
        image = json.loads(marker.read_text())['image_id']
        if image in present:
            candidates.setdefault(image, set())
    items = []
    sizes = {}
    for line in listing.splitlines():
        entry = json.loads(line)
        # Docker reports rounded virtual sizes; image layers can be shared.
        match = re.fullmatch(r'([\d.]+)(B|kB|MB|GB|TB)', entry.get('Size', ''))
        sizes[entry['ID']] = int(float(match[1]) * 1000 ** ('B', 'kB', 'MB', 'GB', 'TB').index(match[2])) if match else None
    for image, tags in candidates.items():
        items.append(dict(id=image, kind='image', name=', '.join(sorted(tags)) or image,
                          bytes=sizes.get(image), protected=image in protected,
                          reason='Referenced by a saved experiment or container' if image in protected else '',
                          tags=sorted(tags)))
    for disk in assets.glob('ball-catching-*.img'):
        protected_disk = disk.is_symlink() or str(disk.resolve()) in paths
        items.append(dict(id=disk.name, kind='disk', name=disk.name,
                          bytes=disk.lstat().st_blocks * 512, protected=protected_disk,
                          reason='Configured, mounted, or referenced by a saved experiment' if protected_disk else ''))
    return sorted(items, key=lambda item: item['bytes'] or 0, reverse=True)


def clean_unused(data, keep_paths=(), asset_ids=None):
    data = Path(data)
    assets = data / 'assets'
    items = asset_inventory(data, keep_paths)
    present = {item['id'] for item in items if item['kind'] == 'image'}
    for marker in assets.glob('image-*.json'):
        if json.loads(marker.read_text())['image_id'] not in present:
            marker.unlink()
    if asset_ids is not None:
        if not isinstance(asset_ids, list) or not asset_ids or any(not isinstance(i, str) for i in asset_ids):
            raise ValueError('Choose at least one asset')
        selected = set(asset_ids)
        eligible = {item['id'] for item in items if not item['protected']}
        if not selected.issubset(eligible):
            raise ValueError('Selected assets are missing or still in use; refresh storage')
        items = [item for item in items if item['id'] in selected]
    removed_images = 0
    for item in items:
        if item['kind'] != 'image' or item['protected']:
            continue
        image, tags = item['id'], item['tags']
        # No force: Docker also protects containers outside this orchestrator.
        try:
            run(['docker', 'image', 'rm', *(tags or [image])], timeout=120)
        except RuntimeError as error:
            if any(reason in str(error).lower() for reason in
                   ('dependent child', 'being used by', 'referenced in multiple repositories')):
                # Ancestors may be retained by containers outside this worker.
                # Keep their registry entry and reconsider on the next cleanup.
                continue
            raise
        (assets / ('image-' + image.removeprefix('sha256:') + '.json')).unlink(missing_ok=True)
        removed_images += 1
    removed_disks = 0
    freed = 0
    for item in items:
        if item['kind'] != 'disk' or item['protected']:
            continue
        disk = assets / item['id']
        freed += disk.stat().st_blocks * 512
        disk.unlink()
        key = disk.stem.removeprefix('ball-catching-')
        (assets / ('disk-provenance-' + key + '.json')).unlink(missing_ok=True)
        removed_disks += 1
    return dict(images=removed_images, disks=removed_disks, disk_bytes=freed)
