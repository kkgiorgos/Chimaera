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
    (d/'metadata.json').write_text(json.dumps(dict(schema_version=3, completed=False,
        finish_reason='robot command timeout', sample_count=1,
        collection_status=dict(command_gap_sim_seconds=1.05, command_timeout_sim_seconds=1.))))
    sys.exit(0)  # ros2 launch can exit zero even when its controller failed.
(d/'metadata.json').write_text(json.dumps(dict(schema_version=3, completed=True, parameters=p)))
(d/'poses.csv').write_text('sim_time,stamp,x,y\\n')
(d/'samples.csv').write_text('elapsed,sim_time\\n0,0\\n1,0\\n')
''')
    executable.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'])
    output = tmp_path/'suite'
    # Fake ROS attempts must not touch a real co-simulation's sockets or lock.
    entrypoint = tmp_path/'runner.py'
    entrypoint.write_text(f"""import sys
sys.path.insert(0, {str(WORKSPACE/'scripts')!r})
import run_experiments as runner
original_open = open
def isolated_open(path, *args, **kwargs):
    if path == '/tmp/chimaera-wall-follow-suite.lock':
        path = {str(tmp_path/'suite.lock')!r}
    return original_open(path, *args, **kwargs)
runner.open = isolated_open
runner.prepare_sockets = lambda: []
sys.exit(runner.main())
""")
    command = [sys.executable, str(entrypoint), '--config', str(config),
               '--architecture', 'testcpu', '--output', str(output), '--collect-only']
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
    result = subprocess.run(command, env=dict(env, FAKE_FAIL='1'), capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Collector: robot command timeout; command receipts: 1' in result.stdout
    failed = json.loads((output/'runs/case_001_rep_01/attempt_001/attempt.json').read_text())
    assert failed['collector_status']['command_gap_sim_seconds'] == 1.05
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


@pytest.mark.parametrize("verbosity", [None, "--quiet", "--verbose"])
def test_gem5_launch_and_resume(suite, tmp_path, verbosity):
    command, env, output = suite
    fake = tmp_path/'ros2'
    fake.write_text(fake.read_text() + '''
if sys.argv[2] == 'wall_follow_bridge':
    (d/'gem5').mkdir()
    (d/'gem5/stats.txt').write_text('---------- Begin Simulation Statistics ----------\\nsimSeconds 1\\nsimInsts 100\\nboard.processor.switch0.core.numCycles 200\\n---------- End Simulation Statistics ----------\\n')
    (d/'timing.csv').write_text('step,sim_seconds,gem5_sim_seconds,gem5_wall_seconds,gazebo_wall_seconds,other_wall_seconds,wall_seconds,elapsed_wall_seconds,startup_wall_seconds\\n1,1,1,.2,.3,.1,1,1,2\\n')
''')
    command += gem5_args(tmp_path)
    if verbosity:
        command += [verbosity]
    subprocess.run(command, env=env, check=True, capture_output=True)
    records = [json.loads(p.read_text()) for p in output.rglob('attempt.json')]
    assert len(records) == 4
    for record in records:
        assert record['command'][2:4] == ['wall_follow_bridge', 'bringup.launch.py']
        assert 'physics_step_ns:=1000000' in record['command']
        assert 'cpu_type:=timing' in record['command']
        assert not any(arg.startswith('ratio:=') for arg in record['command'])
        assert record['gem5_stats']['ipc'] == .5
        assert record['timing']['cosim_realtime_factor'] == 1
        assert record['timing']['gem5_phase_realtime_factor'] == 5
    assert len({next(a for a in r['command'] if a.startswith('outdir:=')) for r in records}) == 4
    subprocess.run(command+['--resume'], env=env, check=True, capture_output=True)
    assert len(list(output.rglob('attempt.json'))) == 4
    assert subprocess.run(command+['--resume', '--interval-us', '100000'], env=env, capture_output=True).returncode != 0


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
    with open(tmp_path/'suite.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(command + gem5_args(tmp_path), env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Another gem5 suite is running' in result.stderr
    assert not output.exists()


def test_hardware_cartesian_plan():
    plan = make_plan(dict(repetitions=1, sweep=dict(cpu_clock=['1GHz', '3GHz'],
                     l1d_size=['8KiB', '32KiB'])), 'timing', gem5=True)
    assert len(plan['runs']) == 4
    assert all(r['parameters']['cpu_type'] == 'timing' for r in plan['runs'])
    for fixed in (dict(cpu_type='kvm'), dict(cpu_clock='0GHz'), dict(num_cores=0),
                  dict(l1d_size='3KiB'), dict(l1_assoc=0), dict(l2_size=256)):
        with pytest.raises(ValueError):
            make_plan(dict(fixed=fixed), 'timing', gem5=True)
    with pytest.raises(ValueError):
        make_plan(dict(fixed=dict(cpu_clock='1GHz')), 'local')


def test_hardware_gem5_dry_run(suite):
    command, env, output = suite
    config = output.parent/'hardware.json'
    config.write_text(json.dumps(dict(repetitions=1, sweep=dict(cpu_clock=['1GHz', '3GHz']))))
    result = subprocess.run(command + ['--gem5', '--dry-run', '--config', str(config)],
                            env=env, check=True, capture_output=True, text=True)
    assert [r['parameters']['cpu_clock'] for r in json.loads(result.stdout)['runs']] == ['1GHz', '3GHz']


def test_verbose_streams_before_completion_and_retains_log(suite, tmp_path):
    import selectors
    command, env, output = suite
    fake = tmp_path/'ros2'
    gate = tmp_path/'continue'
    fake.write_text(fake.read_text().replace('p = dict(', """import time
print('live launch output', flush=True)
gate = pathlib.Path(os.environ['VERBOSE_GATE'])
while not gate.exists():
    time.sleep(.05)
p = dict(""", 1))
    process = subprocess.Popen(command + ['--verbose'], env=dict(env, VERBOSE_GATE=str(gate)),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    prefix = ''
    try:
        import time
        deadline = time.monotonic() + 5
        while 'live launch output' not in prefix and time.monotonic() < deadline:
            if selector.select(.1):
                prefix += os.read(process.stdout.fileno(), 4096).decode()
        assert 'live launch output' in prefix
        assert process.poll() is None  # Output is visible before the child finishes.
        gate.touch()
        rest, _ = process.communicate(timeout=10)
        assert process.returncode == 0, prefix + rest
    finally:
        selector.close()
        gate.touch()
        if process.poll() is None:
            process.kill()
            process.wait()
    assert all('live launch output' in p.read_text() for p in output.rglob('launch.log'))
    subprocess.run(command + ['--resume'], env=env, check=True, capture_output=True)
    assert len(list(output.rglob('attempt.json'))) == 4


def test_live_progress_handles_partial_timing_rows(tmp_path):
    import io
    from run_experiments import LiveProgress
    path = tmp_path/'timing.csv'
    stream = io.StringIO()
    display = LiveProgress(path, stream)
    display.refresh()
    assert 'BOOT / FIRST STEP' in stream.getvalue()
    path.write_text('step,sim_seconds,gem5_wall_seconds\n1,0.05,0.5')
    display.refresh()
    assert display.step == 0
    with path.open('a') as timing:
        timing.write('\n2,0.05,0.25\n')
    display.last_report = 0
    display.refresh()
    assert display.step == 2
    assert display.sim_seconds == .1
    assert 'sim 0.10s | step 2 | gem5 0.200x' in stream.getvalue()
    display.last_report = 0
    display.refresh()
    assert display.sim_seconds == .1  # Reading again must not count intervals twice.
    display.close()


def test_verbose_timeout_preserves_final_output(tmp_path):
    from run_experiments import wait_verbose
    log_path = tmp_path/'launch.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([sys.executable, '-u', '-c',
                                    "import time; print('starting'); time.sleep(10)"],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        with pytest.raises(subprocess.TimeoutExpired):
            wait_verbose(process, log_path, None, .3)
    assert process.poll() is not None
    assert 'starting' in log_path.read_text()


def test_live_progress_terminal_redraw_and_cleanup(tmp_path, monkeypatch):
    import io
    import run_experiments
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setattr(run_experiments.shutil, 'get_terminal_size',
                        lambda **kwargs: os.terminal_size((80, 24)))
    stream = Terminal()
    display = run_experiments.LiveProgress(tmp_path/'timing.csv', stream)
    display.refresh()
    assert '\r\033[2K CHIMAERA | BOOT / FIRST STEP' in stream.getvalue()
    display.output('launch message\n')
    display.refresh()
    assert 'launch message\n\r\033[2K CHIMAERA' in stream.getvalue()
    display.close()
    assert stream.getvalue().endswith('\r\033[2K')


def test_progress_hides_launch_output_and_preserves_logs(suite, tmp_path):
    command, env, output = suite
    fake = tmp_path/'ros2'
    fake.write_text(fake.read_text().replace('p = dict(',
                    "print('child stdout', flush=True)\nprint('child stderr', file=sys.stderr, flush=True)\np = dict(", 1))
    result = subprocess.run(command, env=env, check=True,
                            capture_output=True, text=True)
    assert 'RUNNING | wall' in result.stdout
    assert 'child stdout' not in result.stdout + result.stderr
    assert 'child stderr' not in result.stdout + result.stderr
    for log in output.rglob('launch.log'):
        assert 'child stdout' in log.read_text()
        assert 'child stderr' in log.read_text()
    subprocess.run(command + ['--verbose', '--resume'], env=env, check=True, capture_output=True)
    assert len(list(output.rglob('attempt.json'))) == 4


def test_verbosity_options_are_exclusive(suite):
    command, env, output = suite
    result = subprocess.run(command + ['--quiet', '--verbose'], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'not allowed with argument' in result.stderr
    assert not output.exists()


def test_bundled_sweep_crosses_dimensions_not_bundle_members():
    config = dict(repetitions=2, sweep=dict(
        l1=[dict(l1i_size='8KiB', l1d_size='16KiB'),
            dict(l1i_size='32KiB', l1d_size='64KiB')],
        l2_size=['128KiB', '1MiB']))
    plan = make_plan(config, 'timing', gem5=True)
    assert len(plan['runs']) == 8
    assert plan['dimensions'] == dict(l1=['l1i_size', 'l1d_size'], l2_size=['l2_size'])
    cases = {(r['parameters']['l1i_size'], r['parameters']['l1d_size'], r['parameters']['l2_size'])
             for r in plan['runs']}
    assert cases == {(i,d,l2) for i,d in [('8KiB','16KiB'),('32KiB','64KiB')]
                     for l2 in ['128KiB','1MiB']}
    assert plan['runs'][0]['sweep']['l1'] == config['sweep']['l1'][0]


@pytest.mark.parametrize('config', [
    dict(sweep=dict(l1=[])),
    dict(sweep=dict(l1=[dict(l1i_size='8KiB'), dict(l1d_size='8KiB')])),
    dict(sweep=dict(l1=[dict(l1i_size='8KiB'), '16KiB'])),
    dict(sweep=dict(l1=[{}])),
    dict(sweep=dict(l1=[dict(unknown=1)])),
    dict(sweep=dict(l1=[dict(l1i_size='8KiB')], l1i_size=['16KiB'])),
    dict(fixed=dict(l1i_size='8KiB'), sweep=dict(l1=[dict(l1i_size='16KiB')])),
])
def test_invalid_bundled_dimensions(config):
    with pytest.raises(ValueError):
        make_plan(config, 'timing', gem5=True)


def test_progress_uses_collector_elapsed_and_complete_rows(tmp_path):
    import io
    from run_experiments import LiveProgress
    path = tmp_path/'samples.csv'
    path.write_text('sim_time,elapsed,wall_elapsed\n102,2,10\n104,4,20')
    stream = io.StringIO()
    display = LiveProgress(None, stream, duration=10, samples_path=path)
    display.refresh()
    assert display.sim_seconds == 2
    assert 'task 20%' in stream.getvalue()
    assert 'ETA ~40s' in stream.getvalue()
    with path.open('a') as f:
        f.write('\n')
    display.last_report = 0
    display.refresh()
    assert display.sim_seconds == 4
    assert 'task 40%' in stream.getvalue()
    display.close()


def test_default_progress_and_quiet(suite):
    command, env, output = suite
    result = subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    assert 'RUNNING | wall' in result.stdout
    assert 'control_hz=10' in result.stdout
    assert json.loads((output/'runs/case_001_rep_01/attempt_001/experiment.json').read_text())['sweep'] == {'control_hz': {'control_hz': 10}}
    # A separate suite uses the inferred architecture and summary-only reporting.
    quiet = command[:]
    quiet[quiet.index('--output')+1] = str(output.parent/'quiet')
    arch_index = quiet.index('--architecture')
    del quiet[arch_index:arch_index+2]
    result = subprocess.run(quiet+['--quiet'], env=env, check=True, capture_output=True, text=True)
    assert 'RUNNING | wall' not in result.stdout
    assert json.loads((output.parent/'quiet/suite.json').read_text())['architecture'] == 'local'


def test_extreme_preset_uses_one_grouped_suite():
    config = json.loads((WORKSPACE/'experiments/timing_extreme.json').read_text())
    plan = make_plan(config, 'timing-extreme', gem5=True)
    assert len(plan['runs']) == 4
    assert set(plan['dimensions']) == {'cpu_clock', 'cache_profile'}
    settings = {(r['parameters']['cpu_clock'], r['parameters']['l1i_size'],
                 r['parameters']['l1d_size'], r['parameters']['l2_size']) for r in plan['runs']}
    assert settings == {(clock,i,d,l2) for clock in ('3GHz','100MHz')
                        for i,d,l2 in (('64KiB','64KiB','2MiB'),('1KiB','1KiB','4KiB'))}
