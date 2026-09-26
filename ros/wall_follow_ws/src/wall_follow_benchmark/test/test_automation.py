"""Exercise orchestration with a fake ROS executable; no simulator or sockets required."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

WORKSPACE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE/'scripts'))
from run_experiments import make_plan
from compare_experiments import discover


def test_cartesian_plan_and_validation():
    plan = make_plan(dict(repetitions=2, sweep=dict(control_hz=[10,20], arena_width=[8,12])), 'cpu')
    assert len(plan['runs']) == 8
    assert len({r['id'] for r in plan['runs']}) == 8
    assert plan['runs'][0]['parameters']['control_hz'] == 10.0
    with pytest.raises(ValueError):
        make_plan(dict(sweep=dict(arena_width=[float('nan')])), 'cpu')
    with pytest.raises(ValueError):
        make_plan(dict(fixed=dict(control_hz=20), sweep=dict(control_hz=[10])), 'cpu')


@pytest.fixture
def suite(tmp_path):
    config = tmp_path/'config.json'
    config.write_text(json.dumps(dict(repetitions=2, fixed=dict(duration=1.), sweep=dict(control_hz=[10,20]))))
    executable = tmp_path/'ros2'
    executable.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
p = dict(arg.split(':=', 1) for arg in sys.argv if ':=' in arg)
d = pathlib.Path(p['output_dir'])
if os.environ.get('FAKE_FAIL'):
    sys.exit(0)  # ros2 launch can exit zero even when its controller failed.
(d/'metadata.json').write_text(json.dumps(dict(completed=True, parameters=p)))
(d/'samples.csv').write_text('elapsed,gt_error\\n0,0\\n1,0\\n')
''')
    executable.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'])
    output = tmp_path/'suite'
    command = [sys.executable, str(WORKSPACE/'scripts/run_experiments.py'), '--config', str(config),
               '--architecture', 'testcpu', '--output', str(output), '--no-plot']
    return command, env, output


def test_run_resume_and_mismatch(suite):
    command, env, output = suite
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert len(discover([output])) == 4
    subprocess.run(command+['--resume'], env=env, check=True, capture_output=True)
    assert len(list(output.rglob('attempt.json'))) == 4
    assert subprocess.run(command, env=env, capture_output=True).returncode != 0
    assert subprocess.run(command+['--resume', '--architecture', 'different'], env=env, capture_output=True).returncode != 0


def test_failure_is_retained_and_retried(suite):
    command, env, output = suite
    result = subprocess.run(command, env=dict(env, FAKE_FAIL='1'), capture_output=True)
    assert result.returncode == 1
    assert discover([output]) == []
    subprocess.run(command+['--resume'], env=env, check=True, capture_output=True)
    attempts = list(output.rglob('attempt.json'))
    assert len(attempts) == 5
    assert len(discover([output])) == 4
    assert json.loads((output/'runs/case_001_rep_01/attempt_001/attempt.json').read_text())['status']=='failed'


def test_dry_run_does_not_create_output(suite):
    command, env, output = suite
    subprocess.run(command+['--dry-run'], env=env, check=True, capture_output=True)
    assert not output.exists()
