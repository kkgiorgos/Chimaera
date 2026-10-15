"""No remote listener: authenticated RPC travels through SSH and docker exec."""
import json
import os
import select
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
        output = run(self.base(machine) + [command], input=json.dumps(dict(method=method, **params)).encode(),
                     timeout=610 if method == 'clean_assets' else 40)
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
            raise ValueError('Invalid Docker image ID')
        return value

    def copy_image(self, source, destination, image, cancel=None, timeout=3600, progress=None):
        """Stream a frozen image between Docker stores over two authenticated SSH links."""
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('Invalid Docker image ID')
        def check_cancelled():
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Image transfer cancelled')
        total_bytes = exported_bytes = received_bytes = 0
        streaming_started = None
        def report(phase, sent=0):
            if progress:
                elapsed = time.monotonic() - started
                eta = None
                if phase in {'Complete', 'Already cached'}:
                    eta = 0
                elif phase == 'Streaming' and streaming_started is not None:
                    stream_elapsed = time.monotonic() - streaming_started
                    # Discount compressed bytes still buffered in the controller.
                    forwarded = exported_bytes * min(1, sent / max(received_bytes, 1))
                    if stream_elapsed >= 1 and forwarded > 0 and total_bytes > forwarded:
                        eta = (total_bytes - forwarded) * stream_elapsed / forwarded
                progress(dict(phase=phase, bytes_sent=sent, elapsed_seconds=elapsed,
                              bytes_per_second=sent / max(elapsed, 0.001), eta_seconds=eta,
                              eta_basis='Estimated image size and observed export/network throughput; excludes loading and verification',
                              updated=time.time()))
        started = time.monotonic()
        report('Checking destination cache')
        check_cancelled()
        inspect = shlex.join(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
        present = subprocess.run(self.base(destination) + [inspect], capture_output=True, text=True, timeout=30)
        if present.returncode == 0 and present.stdout.strip() == image:
            report('Already cached')
            return image
        # Carry a content-specific tag through the archive so Docker registers the
        # imported image even when loading into a different image-store backend.
        # Never replace the worker's source-keyed build tags.
        reference = 'chimaera:transfer-' + image.removeprefix('sha256:')
        # Meter uncompressed export bytes before gzip; the final compressed size
        # is not known until EOF. Metadata stays on stderr, outside the archive.
        meter = ('import os,sys,time; last=0; count=0\n'
                 'while True:\n'
                 ' data=os.read(0,262144)\n'
                 ' if not data: break\n'
                 ' sys.stdout.buffer.write(data); sys.stdout.buffer.flush(); count+=len(data)\n'
                 ' if time.monotonic()-last>=1:\n'
                 '  print("CHIMAERA_TRANSFER_BYTES",count,file=sys.stderr,flush=True); last=time.monotonic()\n'
                 'print("CHIMAERA_TRANSFER_BYTES",count,file=sys.stderr,flush=True)')
        export = shlex.join(['bash', '-o', 'pipefail', '-c',
                             shlex.join(['docker', 'image', 'tag', image, reference]) + ' && ' +
                             shlex.join(['docker', 'image', 'inspect', '--format',
                                         'CHIMAERA_TRANSFER_SIZE {{.Size}}', reference]) + ' >&2 && ' +
                             shlex.join(['docker', 'image', 'save', reference]) + ' | ' +
                             shlex.join(['python3', '-c', meter]) + ' | gzip -1'])
        load = shlex.join(['bash', '-o', 'pipefail', '-c', 'gzip -d | docker image load'])
        processes = []
        with tempfile.TemporaryFile() as sender_errors, tempfile.TemporaryFile() as receiver_errors, tempfile.TemporaryFile() as output:
            try:
                sender = subprocess.Popen(self.base(source) + [export], stdout=subprocess.PIPE, stderr=sender_errors)
                processes.append(sender)
                receiver = subprocess.Popen(self.base(destination) + [load], stdin=subprocess.PIPE,
                                            stdout=output, stderr=receiver_errors)
                processes.append(receiver)
                os.set_blocking(sender.stdout.fileno(), False)
                os.set_blocking(receiver.stdin.fileno(), False)
                pending = b''
                eof = False
                sent = 0
                last_report = 0
                streaming_started = time.monotonic()
                report('Streaming')
                deadline = time.monotonic() + timeout
                while any(process.poll() is None for process in processes):
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Image transfer exceeded its time limit')
                    if any(process.poll() not in (None, 0) for process in processes):
                        break
                    readers = [sender.stdout] if not eof and len(pending) < 1024 * 1024 else []
                    writers = [receiver.stdin] if pending and not receiver.stdin.closed else []
                    readable, writable, _ = select.select(readers, writers, [], 0.2)
                    if readable:
                        chunk = os.read(sender.stdout.fileno(), 256 * 1024)
                        pending += chunk
                        received_bytes += len(chunk)
                        eof = not chunk
                    if writable:
                        try:
                            count = os.write(receiver.stdin.fileno(), pending)
                        except BrokenPipeError:
                            receiver.wait(timeout=5)
                            break
                        pending = pending[count:]
                        sent += count
                    if eof and not pending and not receiver.stdin.closed:
                        receiver.stdin.close()
                        report('Loading image', sent)
                    if time.monotonic() - last_report >= 1:
                        # pread leaves the sender's shared stderr write offset intact.
                        size = os.fstat(sender_errors.fileno()).st_size
                        metadata = os.pread(sender_errors.fileno(), min(size, 16384), max(0, size - 16384))
                        for field, value in re.findall(rb'CHIMAERA_TRANSFER_(SIZE|BYTES) ([0-9]+)', metadata):
                            if field == b'SIZE':
                                total_bytes = int(value)
                            else:
                                exported_bytes = int(value)
                        report('Loading image' if receiver.stdin.closed else 'Streaming', sent)
                        last_report = time.monotonic()
                check_cancelled()
                if any(process.poll() != 0 for process in processes):
                    sender_errors.seek(0)
                    receiver_errors.seek(0)
                    output.seek(0)
                    raise RuntimeError('Image transfer failed: source export: ' +
                        sender_errors.read().decode(errors='replace')[-1500:] +
                        '; destination load: ' + receiver_errors.read().decode(errors='replace')[-1500:] +
                        '; load output: ' + output.read().decode(errors='replace')[-1500:])
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                for process in processes:
                    process.wait()
                for process in processes:
                    for stream in (process.stdin, process.stdout):
                        if stream is not None and not stream.closed:
                            stream.close()
            check_cancelled()
            report('Verifying image', sent)
            try:
                imported = self.image_id(destination, reference)
                if imported != image:
                    raise ValueError('Transferred Docker image identity differs: expected ' + image + ', got ' + imported)
                if self.image_id(destination, image) != image:
                    raise ValueError('Transferred Docker image identity differs')
            except (RuntimeError, ValueError) as error:
                output.seek(0)
                raise RuntimeError('Destination image verification failed for ' + image + ': ' + str(error) +
                                   '; docker load output: ' + output.read().decode(errors='replace')[-2000:]) from error
            report('Complete', sent)
        return image
