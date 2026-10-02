"""The default workflow prepares images and selects a reusable immutable guest."""

import json
from pathlib import Path
import sys

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
import guest_assets

def test_selection_preserves_previous_guest_and_resolves_new_default(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_assets, '__file__', str(tmp_path / 'guest_assets.py'))
    assets = tmp_path / 'guest-assets'
    old, new = assets / 'old', assets / 'new'
    old.mkdir(parents=True)
    new.mkdir()
    (old / 'disk.img').write_bytes(b'original guest')
    guest_assets.select('test-profile', old, 'old-image')
    assert guest_assets.selected('test-profile') == old
    guest_assets.select('test-profile', new, 'new-image')
    assert guest_assets.selected('test-profile', 'new-image') == new
    with pytest.raises(ValueError, match='different image'):
        guest_assets.selected('test-profile', 'old-image')
    assert (old / 'disk.img').read_bytes() == b'original guest'
    assert not list(assets.glob('*.tmp'))


def test_missing_or_invalid_selection_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_assets, '__file__', str(tmp_path / 'guest_assets.py'))
    with pytest.raises(ValueError, match='prepare-guest.py first'):
        guest_assets.selected('test-profile')
    pointer = guest_assets.selection_file('test-profile')
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({'directory': '../outside'}))
    with pytest.raises(ValueError, match='Invalid guest selection'):
        guest_assets.selected('test-profile')
