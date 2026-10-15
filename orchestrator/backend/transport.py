"""No remote listener: authenticated RPC travels through SSH and docker exec."""
import json
import re
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

    def image_id(self, machine, image):
        command = shlex.join(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
        value = run(self.base(machine) + [command], text=True, timeout=30).strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
            raise ValueError('Invalid SDK image ID')
        return value

    def copy_image(self, source, destination, image, cancel=None, timeout=3600):
        """Stream a frozen image between Docker stores over two authenticated SSH links."""
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('Invalid SDK image ID')
        def check_cancelled():
            if cancel is not None and cancel.is_set():
                raise RuntimeError('SDK transfer cancelled')
        check_cancelled()
        inspect = shlex.join(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
        present = subprocess.run(self.base(destination) + [inspect], capture_output=True, text=True, timeout=30)
        if present.returncode == 0 and present.stdout.strip() == image:
            return image
        # Save by immutable ID, without importing or replacing any mutable tags.
        export = shlex.join(['bash', '-o', 'pipefail', '-c',
                             shlex.join(['docker', 'image', 'save', image]) + ' | gzip -1'])
        load = shlex.join(['bash', '-o', 'pipefail', '-c', 'gzip -d | docker image load'])
        processes = []
        with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
            try:
                sender = subprocess.Popen(self.base(source) + [export], stdout=subprocess.PIPE, stderr=errors)
                processes.append(sender)
                try:
                    receiver = subprocess.Popen(self.base(destination) + [load], stdin=sender.stdout,
                                                stdout=output, stderr=errors)
                    processes.append(receiver)
                finally:
                    sender.stdout.close()
                deadline = time.monotonic() + timeout
                while any(process.poll() is None for process in processes):
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('SDK transfer exceeded its time limit')
                    if any(process.poll() not in (None, 0) for process in processes):
                        break
                    try:
                        receiver.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                    if receiver.poll() == 0 and sender.poll() is None:
                        try:
                            sender.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            pass
                check_cancelled()
                if any(process.poll() != 0 for process in processes):
                    errors.seek(0)
                    raise RuntimeError('SDK transfer failed: ' + errors.read().decode(errors='replace')[-2000:])
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                for process in processes:
                    process.wait()
        check_cancelled()
        if self.image_id(destination, image) != image:
            raise ValueError('Transferred SDK image identity differs')
        return image
