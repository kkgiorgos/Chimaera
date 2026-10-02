"""Check scheduling, failures, resume, cancellation and the benchmark boundary."""

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('container_runner', DIRECTORY / 'run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def docker_environment(tmp_path):
    executable = tmp_path / 'docker'
    executable.write_text('''#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['image', 'inspect']:
    print('sha256:test')
elif args[:2] == ['container', 'inspect']:
    print('Error: No such container', file=sys.stderr)
    sys.exit(1)
elif args[0] == 'run':
    name = next(a.split('=',1)[1] for a in args if a.startswith('CHIMAERA_JOB='))
    with open(os.environ['EVENTS'], 'a') as f:
        f.write(json.dumps(dict(name=name,event='start',time=time.monotonic()))+'\\n')
    print('benchmark progress ' + name, flush=True)
    time.sleep(float(os.environ.get('JOB_DELAY', '.4')))
    with open(os.environ['EVENTS'], 'a') as f:
        f.write(json.dumps(dict(name=name,event='finish',time=time.monotonic()))+'\\n')
    sys.exit(3 if name == 'bad' and Path(os.environ['FAIL']).exists() else 0)
elif args[0] in ('rm', 'stop'):
    with open(os.environ['EVENTS'], 'a') as f:
        f.write(json.dumps(dict(event=args[0],name=args[-1]))+'\\n')
else:
    sys.exit(2)
''')
    executable.chmod(0o755)
    events = tmp_path / 'events'
    fail = tmp_path / 'fail'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               EVENTS=str(events), FAIL=str(fail))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(image='test:latest', command=['benchmark'],
                                     jobs=[dict(name=n) for n in ('a', 'bad', 'c')])))
    output = tmp_path / 'output'
    argv = [sys.executable, str(DIRECTORY / 'run.py'), '--config', str(config),
            '--output', str(output), '--workers', '2', '--progress-interval', '.1']
    return argv, env, events, fail, output


def test_scheduler_failures_progress_and_resume(docker_environment):
    argv, env, events, fail, output = docker_environment
    fail.touch()
    result = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert 'benchmark progress a' in result.stdout
    records = json.loads((output / 'run.json').read_text())['jobs']
    assert [records[n]['status'] for n in ('a', 'bad', 'c')] == ['completed', 'failed', 'completed']
    recorded = [json.loads(line) for line in events.read_text().splitlines()]
    first_finish = min(e['time'] for e in recorded if e['event'] == 'finish')
    starts = {e['name']: e['time'] for e in recorded if e['event'] == 'start'}
    assert starts['a'] < first_finish and starts['bad'] < first_finish
    assert starts['c'] >= first_finish
    assert len([e for e in recorded if e['event'] == 'rm']) == 3
    fail.unlink()
    result = subprocess.run([*argv, '--resume'], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    recorded = [json.loads(line) for line in events.read_text().splitlines()]
    started = [e['name'] for e in recorded if e['event'] == 'start']
    assert sorted(started[:2]) == ['a', 'bad']
    assert started[2:] == ['c', 'bad']
    config = Path(argv[argv.index('--config') + 1])
    changed = json.loads(config.read_text())
    changed['command'] = ['different']
    config.write_text(json.dumps(changed))
    result = subprocess.run([*argv, '--resume'], env=env, capture_output=True, text=True)
    assert result.returncode == 2 and 'different configuration' in result.stderr


def test_interrupt_cleans_up_running_jobs(docker_environment):
    argv, env, events, fail, output = docker_environment
    env['JOB_DELAY'] = '30'
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not events.exists() or len(events.read_text().splitlines()) < 2:
            if time.monotonic() > deadline:
                pytest.fail('workers did not start')
            time.sleep(.02)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stderr
        state = json.loads((output / 'run.json').read_text())
        assert state['status'] == 'interrupted'
        assert state['jobs']['c']['status'] == 'queued'
        recorded = [json.loads(line) for line in events.read_text().splitlines()]
        assert len([e for e in recorded if e['event'] == 'rm']) == 2
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_dry_run_needs_no_docker_and_writes_no_output(docker_environment):
    argv, env, events, fail, output = docker_environment
    result = subprocess.run([*argv, '--dry-run'], env=env, capture_output=True, text=True, check=True)
    assert len(json.loads(result.stdout)['jobs']) == 3
    assert not output.exists() and not events.exists()


@pytest.mark.parametrize('invalid', [dict(jobs=[dict(name='../escape')]),
    dict(jobs=[dict(name='same'), dict(name='same')]),
    dict(mounts=[dict(source='.', target='/output/subdir')]), dict(cpus=0),
    dict(command='shell string'), dict(unknown=True)])
def test_invalid_config_fails_before_docker(tmp_path, invalid):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(image='test', command=['benchmark'], jobs=[dict(name='one')]) | invalid))
    with pytest.raises(ValueError):
        runner.load(config)


def test_wall_follow_export_uses_current_adapter(tmp_path):
    inputs = tmp_path / 'inputs'
    overlay = tmp_path / 'overlay'
    overlay.mkdir()
    subprocess.run([sys.executable, str(DIRECTORY / 'wall-follow/configure.py'),
                    '--local', '--overlay', str(overlay), '--output', str(inputs)], check=True, capture_output=True)
    config = runner.load(inputs / 'runner.json')
    assert not config['devices'] and len(config['jobs']) > 1
    assert all(Path(mount['source']).exists() for mount in config['mounts'])
    job = config['jobs'][-1]
    command = config['command'] + job['args'] + ['--dry-run']
    replacements = {'/assets/workspace/scripts/run_experiments.py': str(DIRECTORY.parent / 'ros/wall_follow_ws/scripts/run_experiments.py'),
                    '/assets/experiment.json': str(inputs / 'experiment.json'),
                    '/output/suite': str(tmp_path / 'suite')}
    result = subprocess.run([sys.executable, *[replacements.get(arg, arg) for arg in command[1:]]],
                            capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan['selected_run_ids'] == [job['name']]
    assert not (tmp_path / 'suite').exists()
