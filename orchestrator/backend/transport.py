"""No remote listener: authenticated RPC travels through SSH and docker exec."""
import json
import shlex
import subprocess

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

    def fetch(self, machine, path, destination):
        # tar streams over SSH; extraction and path/link checks happen locally.
        command = shlex.join(['docker', 'exec', 'chimaera-supervisor', 'tar',
                              '-C', path, '-chSzf', '-', '.'])
        with destination.open('wb') as stream:
            result = subprocess.run(self.base(machine) + [command], stdout=stream,
                                    stderr=subprocess.PIPE, timeout=1800)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])

    def push(self, machine, source, destination):
        command = shlex.join(['docker', 'exec', '-i', 'chimaera-supervisor',
                              'tar', '-xzf', '-', '-C', destination])
        with source.open('rb') as stream:
            result = subprocess.run(self.base(machine) + [command], stdin=stream,
                                    stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, timeout=1800)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
