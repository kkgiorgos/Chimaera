from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from common import atomic_json
from guest import assemble, ensure_assets


@pytest.mark.parametrize('target_exists', [True, False])
def test_assemble_replaces_absolute_init_symlink_without_touching_its_target(tmp_path, target_exists):
    work = tmp_path / 'work'
    work.mkdir()
    script = '#!/bin/bash\necho CHIMAERA_GUEST_READFILE_READY\n'
    (work / 'init.sh').write_text(script)
    outside = tmp_path / 'helper-systemd'
    if target_exists:
        outside.write_text('helper systemd executable')
    inspected = []

    def execute(argv, **kwargs):
        if argv[0] == 'tar':
            root = Path(argv[-1])
            for relative in ('usr/sbin', 'usr/local/bin', 'usr/lib/systemd', 'etc',
                             'opt/chimaera/gem5/util/m5/build/x86/out'):
                (root / relative).mkdir(parents=True, exist_ok=True)
            (root / 'sbin').symlink_to('usr/sbin')
            (root / 'usr/sbin/init').symlink_to(outside)
            (root / 'usr/lib/systemd/systemd').write_text('guest systemd executable')
            (root / 'opt/chimaera/gem5/util/m5/build/x86/out/m5').write_text('m5')
        elif argv[0] == 'mke2fs':
            root = Path(argv[argv.index('-d') + 1])
            init = root / 'usr/sbin/init'
            assert not init.is_symlink()
            assert (root / 'sbin/init').read_text() == script
            assert init.stat().st_mode & 0o777 == 0o755
            assert (root / 'usr/lib/systemd/systemd').read_text() == 'guest systemd executable'
            inspected.append(True)
        return subprocess.CompletedProcess(argv, 0)

    with patch('guest.subprocess.run', side_effect=execute):
        assemble(work)
    assert inspected == [True]
    assert outside.exists() == target_exists
    if target_exists:
        assert outside.read_text() == 'helper systemd executable'


def test_guest_recipe_change_rebuilds_disk_without_removing_previous_cache(tmp_path):
    policy = tmp_path / 'containers/wall-follow/profiles/jammy-humble-fortress.guest.json'
    atomic_json(policy, dict(kernel={}))
    init = tmp_path / 'containers/wall-follow/scripts/guest-init.sh'
    init.parent.mkdir(parents=True)
    init.write_text("#!/bin/bash\nif grep -qw 'chimaera.probe=1' /proc/cmdline; then\ntrue\nfi\nworkload_pid=\n")
    recipe = tmp_path / 'orchestrator/backend/guest.py'
    recipe.parent.mkdir(parents=True)
    recipe.write_text('old recipe')
    kernel = tmp_path / 'kernel'
    kernel.write_bytes(b'kernel')
    builds = []

    def execute(argv, **kwargs):
        if '--assemble-stream' in argv:
            mount = argv[argv.index('-v') + 1]
            work = Path(mount.removesuffix(':/work'))
            (work / 'disk.img').write_bytes(recipe.read_bytes())
            builds.append(work)
        return subprocess.CompletedProcess(argv, 0)

    with patch('guest.run', return_value='sha256:cached-application'), \
         patch('guest.subprocess.Popen') as export, \
         patch('guest.subprocess.run', side_effect=execute):
        export.return_value.wait.return_value = 0
        export.return_value.poll.return_value = 0
        first, _ = ensure_assets(tmp_path, tmp_path / 'experiment', 'cached-image', kernel=kernel)
        again, _ = ensure_assets(tmp_path, tmp_path / 'experiment', 'cached-image', kernel=kernel)
        assert again == first
        assert len(builds) == 1
        recipe.write_text('fixed init recipe')
        second, _ = ensure_assets(tmp_path, tmp_path / 'experiment', 'cached-image', kernel=kernel)
    assert len(builds) == 2
    assert second != first
    assert first.read_bytes() == b'old recipe'
    assert second.read_bytes() == b'fixed init recipe'
