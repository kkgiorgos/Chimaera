"""Opt-in real Docker lifecycle check using synthetic cases, never simulations.

Run inside the supervisor image with /repo read-only, the Docker socket, and
/tmp/chimaera-lifecycle mounted at that same host/container path. See README.
"""
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, '/app')
from agent import Agent
from common import atomic_json


def main():
    base = Path('/tmp/chimaera-lifecycle')
    root = Path('/repo')
    experiment = 'smoke-' + uuid.uuid4().hex[:12]
    image = os.environ.get('SMOKE_IMAGE', 'chimaera-supervisor:orchestrator-check')
    agent = Agent(root, base / 'state')
    directory = agent.directory(experiment)
    inputs = directory / 'inputs'
    inputs.mkdir(parents=True)
    atomic_json(directory / 'ready.json', {})
    code = '''import json,time,sys
from pathlib import Path
print('worker stdout',flush=True)
print('worker stderr',file=sys.stderr,flush=True)
time.sleep(3)
p=Path('/output/suite'); p.mkdir()
a=p/'case-000-attempt-001'; a.mkdir()
(a/'result.json').write_text(json.dumps({'success':False}))
(p/'experiment_manifest.json').write_text(json.dumps({'points':[{'id':'case-000','status':'complete','trials':['case-000-attempt-001']}]}))
'''
    atomic_json(inputs / 'runner.json', dict(image=image, command=['python3', '-c', code],
        jobs=[dict(name='case-000')], devices=[], mounts=[], cpus=1, memory='2g'))
    try:
        attempt = uuid.uuid4().hex
        task = agent.start(experiment, 'case-000', attempt, 1, '2g')
        assert task['status'] == 'running', task
        assert agent.start(experiment, 'case-000', attempt, 1, '2g')['id'] == attempt
        agent = Agent(root, base / 'state')
        for _ in range(15):
            agent.reconcile()
            task = agent.store.get('tasks', attempt)
            if task['status'] == 'completed':
                break
            time.sleep(1)
        assert task['status'] == 'completed', task
        log = (Path(task['output']) / 'worker.log').read_text()
        assert 'worker stdout' in log and 'worker stderr' in log
        second = agent.start(experiment, 'case-000', uuid.uuid4().hex, 1, '2g')
        agent.stop(experiment, generation=0)
        assert agent.store.get('tasks', second['id'])['status'] == 'stopped'
        try:
            agent.start(experiment, 'case-000', uuid.uuid4().hex, 1, '2g')
        except ValueError:
            pass
        else:
            raise AssertionError('Late assignment accepted after stop')
    finally:
        agent.stop(experiment)
        agent.cleanup(experiment)
    print(json.dumps(dict(detached_completion=True, supervisor_reopen=True,
        duplicate_prevention=True, stdout_and_stderr=True, immediate_stop=True,
        stop_barrier=True, cleanup=True)))


if __name__ == '__main__':
    main()
