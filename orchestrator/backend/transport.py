"""No remote listener: authenticated RPC travels through SSH and docker exec."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from common import run


class SSH:
    def remove(self, machine, cleanup):
        from removal import validate_cleanup
        validate_cleanup(cleanup)
        helper = Path(__file__).with_name('removal.py').read_text()
        helper += '\nimport sys\nrequest = json.load(sys.stdin)\nremove(**request)\n'
        command = shlex.join(['python3', '-c', helper])
        run(self.base(machine) + [command],
            input=json.dumps(dict(machine=machine, cleanup=cleanup)).encode(), timeout=1200)
        return True

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
                   'CHIMAERA_KVM_GID=$(stat -c %g /dev/kvm 2>/dev/null || true) '
                   'docker compose -f orchestrator/worker.compose.yaml up -d --build')
        run(self.base(machine) + [command], timeout=1200)

    def call(self, machine, method, **params):
        command = shlex.join(['docker', 'exec', '-i', 'chimaera-supervisor',
                              'python3', '/app/agent.py', 'rpc'])
        output = run(self.base(machine) + [command], input=json.dumps(dict(method=method, **params)).encode(),
                     timeout=610 if method in {'clean_assets', 'release', 'commit_assets'} else 40)
        result = json.loads(output)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['result']

    def fetch(self, machine, path, destination, cancel=None):
        # Calibration is already frozen in the experiment's calibration.json.
        # The per-case link targets /assets inside the worker, not the supervisor.
        # Do not dereference that link (including for existing failed attempts).
        command = shlex.join(['docker', 'exec', 'chimaera-supervisor', 'tar',
                              '-C', path, '--exclude=./suite/guest.tar.gz',
                              '--exclude=./suite/calibration',
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
            raise ValueError('Invalid Docker image ID')
        return value

    def copy_readiness(self, source, destination, experiment, expected, cancel=None, timeout=3600):
        if cancel is not None and cancel.is_set():
            raise RuntimeError('Readiness transfer cancelled')
        directory = self.call(destination, 'asset_destination', experiment=experiment)
        helper = Path(__file__).with_name('readiness_transfer.py').read_text()
        path = str(Path(source['repo']) / '.chimaera-orchestrator/experiments' / experiment / 'export-assets')
        command = shlex.join(['python3', '-c', helper, path, directory,
                             json.dumps(self.base(destination)), str(timeout)])
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.base(source) + [command], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=errors)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError('Readiness transfer cancelled')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Readiness transfer exceeded its time limit')
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode(errors='replace')[-4000:])
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if cancel is not None and cancel.is_set():
            raise RuntimeError('Readiness transfer cancelled')
        self.call(destination, 'commit_assets', experiment=experiment, expected=expected)

    def copy_image(self, source, destination, image, cancel=None, timeout=3600, progress=None):
        """Coordinate a direct producer-to-destination stream; receive only progress."""
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('Invalid Docker image ID')

        def check_cancelled():
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Image transfer cancelled')

        check_cancelled()
        if progress:
            progress(dict(phase='Checking destination cache', bytes_sent=0,
                          elapsed_seconds=0, bytes_per_second=0, eta_seconds=None,
                          eta_basis='', updated=time.time()))
        inspect = shlex.join(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
        present = subprocess.run(self.base(destination) + [inspect], capture_output=True, text=True, timeout=30)
        if present.returncode == 0 and present.stdout.strip() == image:
            if progress:
                progress(dict(phase='Already cached', bytes_sent=0, elapsed_seconds=0,
                              bytes_per_second=0, eta_seconds=0, eta_basis='', updated=time.time()))
            return image
        check_cancelled()
        # Ship the helper as code so workers need no synchronized helper install.
        # Its stdin remains open as a cancellation/disconnection signal.
        helper = Path(__file__).with_name('image_transfer.py').read_text()
        command = shlex.join(['python3', '-c', helper, image,
                              json.dumps(self.base(destination)), str(timeout)])
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.base(source) + [command], stdin=subprocess.PIPE,
                                       stdout=output, stderr=errors)
            deadline = time.monotonic() + timeout
            offset = 0
            pending = b''
            imported = None

            def read_progress():
                nonlocal offset, pending, imported
                data = os.pread(output.fileno(), 65536, offset)
                offset += len(data)
                pending += data
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    value = json.loads(line)
                    if value.get('phase') in {'Complete', 'Already cached'}:
                        imported = value.get('image_id') or image
                        if not re.fullmatch(r'sha256:[0-9a-f]{64}', imported):
                            raise ValueError('Invalid transferred Docker image ID')
                    if progress:
                        progress(value)
                return bool(data)

            try:
                while process.poll() is None:
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Image transfer exceeded its time limit')
                    read_progress()
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                check_cancelled()
                while read_progress():
                    pass
                if process.returncode:
                    errors.seek(0)
                    raise RuntimeError('Direct image transfer failed: ' +
                                       errors.read().decode(errors='replace')[-4000:])
            finally:
                # Closing stdin lets the producer reap its export and destination
                # SSH processes before we terminate the control connection.
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if imported is None:
            raise RuntimeError('Image transfer completed without destination verification')
        return imported
