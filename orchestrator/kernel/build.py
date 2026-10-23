"""Build the guest ELF kernel and external driver in one disposable build tree."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def build(jobs, backend='dma'):
    if backend not in ('dma', 'm5ops'):
        raise ValueError('Unsupported kernel/module backend: ' + backend)
    driver_name = 'chimaera_m5' if backend == 'm5ops' else 'chimaera_dma'
    recipe, output = Path('/recipe'), Path('/output')
    policy = json.loads((recipe / 'policy.json').read_text())
    archive = Path('/source/linux.tar.xz')
    if sha256(archive) != policy['sha256']:
        raise ValueError('Linux source checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='kernel-', dir='/tmp') as temporary:
        work = Path(temporary)
        # This exact archive was hash-verified above. GNU tar preserves the
        # kernel's cross-architecture source symlinks (Python's data filter
        # rejects some legitimate links in this upstream release).
        subprocess.run(['tar', '--extract', '--xz', '--no-same-owner',
                        '--file=' + str(archive), '--directory=' + str(work)], check=True)
        tree = work / ('linux-' + policy['version'])
        module = work / 'driver'
        module.mkdir()
        for name in ('Makefile', driver_name + '.c'):
            shutil.copyfile(Path('/driver') / name, module / name)
        make = ['make', '-C', str(tree), 'ARCH=x86_64']
        subprocess.run([*make, 'x86_64_defconfig'], check=True)
        with (tree / '.config').open('a') as config:
            config.write('\n' + (recipe / 'config.fragment').read_text())
        subprocess.run([*make, 'olddefconfig'], check=True)
        config = (tree / '.config').read_text()
        for option in ('MODULES', 'MODVERSIONS', 'ATA_PIIX', 'EXT4_FS', 'DEVTMPFS',
                       'DEVMEM', 'SERIAL_8250_CONSOLE'):
            if f'CONFIG_{option}=y\n' not in config:
                raise ValueError('Required guest kernel option missing: ' + option)
        # A full build supplies Module.symvers; modules_prepare alone does not.
        subprocess.run([*make, '-j' + str(jobs), 'vmlinux', 'modules'], check=True)
        # Kbuild rewrites obj-m internally. A command-line obj-m override
        # prevents those assignments and breaks external module target paths.
        subprocess.run([*make, '-j' + str(jobs), 'M=' + str(module),
                        'GEM5_INCLUDE=/gem5/include',
                        'CHIMAERA_MODULES=' + driver_name + '.o', 'modules'], check=True)
        release = subprocess.check_output([*make, '-s', 'kernelrelease'], text=True).strip()
        driver = module / (driver_name + '.ko')
        vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(driver)], text=True).strip()
        if vermagic.split()[0] != release:
            raise ValueError('Built transport module does not match the guest kernel release')
        if backend == 'm5ops':
            header = Path('/gem5/include/gem5/chimaera_m5.h').read_text()
            if '#define CHM5_ABI_VERSION 1\n' not in header:
                raise ValueError('m5ops preparation requires module ABI 1')
        for name, source in [('kernel', tree / 'vmlinux'), (driver_name + '.ko', driver),
                             ('kernel.config', tree / '.config'), ('Module.symvers', tree / 'Module.symvers')]:
            shutil.copyfile(source, output / name)
        (output / 'build.json').write_text(json.dumps(dict(kernel_release=release, vermagic=vermagic,
            backend=backend, module=driver_name + '.ko', abi_version=1,
            source=policy, compiler=subprocess.check_output(['gcc', '--version'], text=True).splitlines()[0]),
            indent=2) + '\n')


if __name__ == '__main__':
    build(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else 'dma')
