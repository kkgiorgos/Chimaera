import json
from unittest.mock import patch

import pytest

from checkpoints import unpack
from common import digest


def test_import_checks_disk_identity_and_does_not_mark_corrupt_assets_ready(tmp_path):
    image = 'sha256:' + 'a' * 64
    directory = tmp_path / 'readiness-assets'
    def execute(command, **kwargs):
        if command[:2] == ['docker', 'cp']:
            (directory / 'checkpoints').mkdir()
            for name in ('guest.img', 'kernel', 'guest.tar.gz'):
                (directory / name).write_text(name)
            index = dict(provenance=dict(disk_sha256='different disk',
                kernel_sha256=digest(directory / 'kernel'),
                bundle_sha256=digest(directory / 'guest.tar.gz')))
            (directory / 'checkpoints/index.json').write_text(json.dumps(index))
        return None
    # Build the expected index independently; image bytes must match this identity.
    fixture = tmp_path / 'expected.json'
    kernel = tmp_path / 'kernel'
    bundle = tmp_path / 'bundle'
    kernel.write_text('kernel')
    bundle.write_text('guest.tar.gz')
    fixture.write_text(json.dumps(dict(provenance=dict(disk_sha256='different disk',
        kernel_sha256=digest(kernel), bundle_sha256=digest(bundle)))))
    with patch('checkpoints.subprocess.run', side_effect=execute) as commands:
        with pytest.raises(ValueError, match='asset differs: guest.img'):
            unpack(tmp_path, image, digest(fixture))
    assert not (directory / 'import.json').exists()
    assert any(call.args[0][:2] == ['docker', 'rm'] for call in commands.call_args_list)
