"""Build a ball-catching guest from its Ubuntu application image without host mounts.

The rootfs retains the image's ROS runtime. The application bundle is injected at
boot by the existing benchmark. A pinned prebuilt gem5 kernel is downloaded and
verified when absent; rebuilding Linux itself is unnecessary.
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid

from common import atomic_json, digest, run


def assemble(work):
    with tempfile.TemporaryDirectory(dir=work, prefix='rootfs-') as temporary:
        root = Path(temporary)
        # Trusted archive exported from our locally built image, extracted in a helper.
        subprocess.run(['tar', '--no-same-owner', '-xf', str(work / 'rootfs.tar'), '-C', temporary], check=True)
        for tree in ('src', 'tmp', 'var/lib/apt/lists'):
            shutil.rmtree(root / tree, ignore_errors=True)
        for directory in ('tmp', 'run', 'dev', 'proc', 'sys'):
            (root / directory).mkdir(exist_ok=True)
        (root / 'tmp').chmod(0o1777)
        init = (work / 'init.sh').read_text()
        (root / 'usr/sbin/init').write_text(init)
        (root / 'usr/sbin/init').chmod(0o755)
        shutil.copy2(root / 'opt/chimaera/gem5/util/m5/build/x86/out/m5', root / 'usr/local/bin/m5')
        (root / 'etc/fstab').write_text('')
        (root / 'etc/hostname').write_text('chimaera-guest\n')
        (root / 'etc/hosts').write_text('127.0.0.1 localhost chimaera-guest\n')
        filesystem = work / 'root.ext4'
        # Headroom for the injected ROS bundle. Sparse storage avoids allocating holes.
        total = sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())
        size = max(8 * 1024**3, ((total * 2 // 1024**3) + 2) * 1024**3)
        with filesystem.open('wb') as stream:
            stream.truncate(size)
        subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-m', '0', '-O', '^metadata_csum_seed,^orphan_file',
            '-E', 'lazy_itable_init=0,lazy_journal_init=0', '-d', temporary, str(filesystem)], check=True)
        subprocess.run(['e2fsck', '-f', '-n', str(filesystem)], check=True)
        offset = 2 * 1024**2
        mbr = bytearray(512)
        struct.pack_into('<I', mbr, 440, 0x4348494d)
        for index, start, count in ((0, 2048, 2048), (1, 4096, size // 512)):
            struct.pack_into('<B3sB3sII', mbr, 446 + 16 * index, 0, b'\xfe\xff\xff',
                             0x83, b'\xfe\xff\xff', start, count)
        mbr[510:] = b'\x55\xaa'
        with (work / 'disk.img').open('wb') as target:
            target.write(mbr)
            target.truncate(offset + size)
        # GNU dd preserves holes in the large sparse guest disk.
        subprocess.run(['dd', 'if=' + str(filesystem), 'of=' + str(work / 'disk.img'),
            'bs=1M', 'seek=2', 'conv=notrunc,sparse', 'status=none'], check=True)
        filesystem.unlink()


def ensure_assets(root, directory, image, disk=None, kernel=None):
    root, directory = Path(root), Path(directory)
    policy = json.loads((root / 'containers/wall-follow/profiles/jammy-humble-fortress.guest.json').read_text())['kernel']
    assets = root / '.chimaera-orchestrator/assets'
    assets.mkdir(parents=True, exist_ok=True)
    if kernel:
        kernel = Path(kernel)
        if not kernel.is_file():
            raise ValueError('Configured kernel does not exist')
    else:
        kernel = assets / policy['resource']
        if not kernel.exists():
            temporary = kernel.with_suffix('.download')
            with urllib.request.urlopen(policy['url'], timeout=60) as source, temporary.open('wb') as target:
                shutil.copyfileobj(source, target)
            if digest(temporary) != policy['sha256']:
                raise ValueError('Downloaded kernel checksum mismatch')
            temporary.replace(kernel)
        if digest(kernel) != policy['sha256']:
            raise ValueError('Cached kernel checksum mismatch')
    if disk:
        disk = Path(disk)
        if not disk.is_file():
            raise ValueError('Configured guest disk does not exist')
        return disk, kernel
    # Reuse the disk produced on this host; cluster peers receive this frozen asset.
    disk = assets / 'ball-catching.img'
    if disk.exists():
        return disk, kernel
    work = assets / ('build-' + uuid.uuid4().hex[:12])
    work.mkdir()
    name = 'chimaera-guest-' + uuid.uuid4().hex[:12]
    try:
        run(['docker', 'create', '--name', name, '--label',
             'chimaera.experiment=' + directory.name, image])
        subprocess.run(['docker', 'export', '-o', str(work / 'rootfs.tar'), name], check=True)
        init = (root / 'containers/wall-follow/scripts/guest-init.sh').read_text()
        # Remove the wall-follow-only diagnostic probe, retaining generic boot/readfile handling.
        start = init.index("if grep -qw 'chimaera.probe=1'")
        end = init.index('\nworkload_pid=', start)
        (work / 'init.sh').write_text(init[:start] + init[end:])
        # mkfs populates root-owned guest files in a disposable root helper.
        subprocess.run(['docker', 'run', '--rm', '--label', 'chimaera.experiment=' + directory.name,
            '-v', f'{work}:/work', 'chimaera-supervisor:local',
            'python3', '/app/guest.py', '--assemble'], check=True)
        (work / 'disk.img').replace(disk)
        atomic_json(assets / 'disk-provenance.json', dict(image=image, sha256=digest(disk)))
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        # Root helper must remove its own root-owned intermediate tree on failure.
        subprocess.run(['docker', 'run', '--rm', '-v', f'{assets}:/assets',
            'chimaera-supervisor:local', 'python3', '-c',
            'import shutil; shutil.rmtree(' + repr('/assets/' + work.name) + ', ignore_errors=True)'], capture_output=True)
    return disk, kernel


if __name__ == '__main__' and sys.argv[1:] == ['--assemble']:
    assemble(Path('/work'))
