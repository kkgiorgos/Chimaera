"""Checkpoint sharing and fail-closed integrity, without booting a guest."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from readiness_checkpoint import freeze, groups, key, validate
from run_memory_suite import load_config, plan


def configuration():
    return load_config(Path(__file__).resolve().parents[1] / 'experiments/memory_contention_patterns.json')


def test_ball_parameters_pressure_and_pattern_share_one_checkpoint():
    data = configuration()
    points = plan(data)
    varied = deepcopy(points[-1])
    varied['throw'] = dict(name='other', launch_position=[2, 1, 4],
                          launch_direction=[-1, 0.1, 2], launch_speed=7)
    assert len(groups(data, [*points, varied])) == 1
    assert key(data, varied) == key(data, points[0])


def test_hardware_and_camera_or_controller_changes_require_compatible_checkpoints():
    data = configuration()
    point = plan(data)[0]
    original = key(data, point)
    changed = deepcopy(point)
    changed['rates']['control_hz'] = 200
    assert key(data, changed) != original
    for section, field, value in [('hardware', 'num_cores', 2),
                                   ('fixed', 'camera_width', 640)]:
        changed_data = deepcopy(data)
        changed_data[section][field] = value
        assert key(changed_data, point) != original


def checkpoint(tmp_path):
    data = configuration()
    point = plan(data)[0]
    directory = tmp_path / key(data, point)
    (directory / 'cpt').mkdir(parents=True)
    (directory / 'cpt/m5.cpt').write_text('guest state')
    (directory / 'checkpoint.json').write_text(json.dumps(dict(aggressor_enabled=False)))
    for name in ('host-replay.bin', 'checkpoint-host-state', 'checkpoint-ready'):
        (directory / name).write_text('host state')
    identity = dict(gem5_sha256='simulator', disk_sha256='exact backing disk')
    freeze(tmp_path, identity, [directory.name])
    return data, point, identity, directory


def test_checkpoint_requires_matching_disk_and_unchanged_host_and_guest_state(tmp_path):
    data, point, identity, directory = checkpoint(tmp_path)
    assert validate(tmp_path, data, point, identity) == directory
    with pytest.raises(ValueError, match='execution artifacts differ'):
        validate(tmp_path, data, point, dict(identity, disk_sha256='other disk'))
    (directory / 'host-replay.bin').write_text('corrupted host state')
    with pytest.raises(ValueError, match='checkpoint changed'):
        validate(tmp_path, data, point, identity)


def test_incomplete_or_aggressed_checkpoints_are_rejected(tmp_path):
    data, point, identity, directory = checkpoint(tmp_path)
    (directory / 'checkpoint.json').write_text(json.dumps(dict(aggressor_enabled=True)))
    with pytest.raises(ValueError, match='incomplete or contains aggressor'):
        freeze(tmp_path, identity, [directory.name])
