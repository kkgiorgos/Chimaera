"""Exercise orchestration with a fake ROS executable; no simulator or sockets required."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
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
(d/'metadata.json').write_text(json.dumps(dict(schema_version=2, completed=True, parameters=p)))
(d/'poses.csv').write_text('sim_time,stamp,x,y\\n')
(d/'samples.csv').write_text('elapsed,sim_time\\n0,0\\n1,0\\n')
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
    prepared = json.loads(next(output.rglob('controller.yaml')).read_text())
    assert 'duration' not in prepared['wall_follower']['ros__parameters']
    assert set(prepared) == {'wall_follower'}
    attempt = json.loads(next(output.rglob('attempt.json')).read_text())
    assert 'duration:=1.0' in attempt['command']
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


def test_host_only_selects_independent_launch(suite):
    command, env, output = suite
    subprocess.run(command + ['--host-only'], env=env, check=True, capture_output=True)
    record = json.loads(next(output.rglob('attempt.json')).read_text())
    assert record['command'][3] == 'host.launch.py'
    assert json.loads((output/'suite.json').read_text())['deployment'] == 'host_only'


def gem5_args(tmp_path):
    root = tmp_path/'gem5'
    for name in ('build/X86/gem5.opt', 'resources/x86-ubuntu-22.04-ros-humble.img',
                 'resources/x86-linux-kernel-5.15.180'):
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return ['--gem5', '--gem5-root', str(root)]


def test_gem5_launch_and_resume(suite, tmp_path):
    command, env, output = suite
    fake = tmp_path/'ros2'
    fake.write_text(fake.read_text() + '''
if sys.argv[2] == 'wall_follow_bridge':
    (d/'timing.csv').write_text('step,sim_seconds,gem5_sim_seconds,gem5_wall_seconds,gazebo_wall_seconds,other_wall_seconds,pacing_wall_seconds,wall_seconds,elapsed_wall_seconds,startup_wall_seconds\\n1,1,1,.2,.3,.1,.4,1,1,2\\n')
''')
    command += gem5_args(tmp_path)
    subprocess.run(command, env=env, check=True, capture_output=True)
    records = [json.loads(p.read_text()) for p in output.rglob('attempt.json')]
    assert len(records) == 4
    for record in records:
        assert record['command'][2:4] == ['wall_follow_bridge', 'bringup.launch.py']
        assert 'physics_step_ns:=1000000' in record['command']
        assert record['timing']['cosim_realtime_factor'] == 1
        assert record['timing']['gem5_phase_realtime_factor'] == 5
    assert len({next(a for a in r['command'] if a.startswith('outdir:=')) for r in records}) == 4
    subprocess.run(command+['--resume'], env=env, check=True, capture_output=True)
    assert len(list(output.rglob('attempt.json'))) == 4
    assert subprocess.run(command+['--resume', '--ratio', '2'], env=env, capture_output=True).returncode != 0


def test_gem5_requires_timing(suite, tmp_path):
    command, env, output = suite
    result = subprocess.run(command + gem5_args(tmp_path), env=env, capture_output=True)
    assert result.returncode == 1
    record = json.loads(next(output.rglob('attempt.json')).read_text())
    assert record['status'] == 'failed'
    assert 'timing_error' in record


def test_gem5_invalid_interval_and_dry_run(suite):
    command, env, output = suite
    result = subprocess.run(command+['--gem5', '--dry-run'], env=env, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['deployment'] == 'gem5'
    assert not output.exists()
    assert subprocess.run(command+['--gem5', '--dry-run', '--interval-us', '50100'], env=env, capture_output=True).returncode != 0
    assert subprocess.run(command+['--gem5', '--host-only', '--dry-run'], env=env, capture_output=True).returncode != 0


def test_gem5_refuses_concurrent_suite(suite, tmp_path):
    import fcntl
    command, env, output = suite
    with open('/tmp/chimaera-wall-follow-suite.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(command + gem5_args(tmp_path), env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Another gem5 suite is running' in result.stderr
    assert not output.exists()
