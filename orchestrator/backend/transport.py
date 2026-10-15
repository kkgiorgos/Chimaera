"""No remote listener: authenticated RPC travels through SSH and docker exec."""
import json
import shlex
import subprocess
import tempfile
import time

from common import run


class SSH:
    def base(self, machine):
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
                '-o', 'ServerAliveCountMax=2', '-p', str(machine['port']),
                '--', machine['target']]

    def deploy(self, machine):
        repo = shlex.quote(machine['repo'])
        # Host paths must stay identical inside the supervisor and helper containers.
        command = (f'cd {repo} && CHIMAERA_REPO={repo} CHIMAERA_UID=$(id -u) '
                   'CHIMAERA_GID=$(id -g) CHIMAERA_DOCKER_GID=$(stat -c %g /var/run/docker.sock) '
                   'docker compose -f orchestrator/worker.compose.yaml up -d --build')
        run(self.base(machine) + [command], timeout=1200)

    def call(self, machine, method, **params):
        command = shlex.join(['docker', 'exec', '-i', 'chimaera-supervisor',
                              'python3', '/app/agent.py', 'rpc'])
        output = run(self.base(machine) + [command], input=json.dumps(dict(method=method, **params)).encode(), timeout=40)
        result = json.loads(output)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['result']

    def fetch(self, machine, path, destination, cancel=None):
        # tar streams over SSH; extraction and path/link checks happen locally.
        command = shlex.join(['docker', 'exec', 'chimaera-supervisor', 'tar',
                              '-C', path, '--exclude=./suite/guest.tar.gz',
                              '-chSzf', '-', '.'])
        if cancel is not None and cancel.is_set():
            raise RuntimeError('Result collection cancelled')
        with destination.open('wb') as stream, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.base(machine) + [command], stdout=stream, stderr=errors)
            deadline = time.monotonic() + 1800
            try:
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError('Result collection cancelled')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Result collection exceeded 30 minutes')
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode(errors='replace')[-2000:])
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
