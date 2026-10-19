"""Stream sparse readiness files directly between hosts, without an archive on disk."""
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time


def transfer(source, destination, ssh, timeout=3600):
    export = ['tar', '--dereference', '--sparse', '--format=pax', '--sparse-version=1.0',
              '--mode=a+rX', '-czf', '-', '-C', source, '.']
    load = shlex.join(['docker', 'exec', '-i', 'chimaera-supervisor',
                      'tar', '--extract', '--gzip', '--sparse', '--no-same-owner',
                      '--file=-', '--directory=' + destination])
    processes = []
    with tempfile.TemporaryFile() as errors:
        try:
            sender = subprocess.Popen(export, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            processes.append(sender)
            receiver = subprocess.Popen([*ssh, load], stdin=sender.stdout, stdout=subprocess.DEVNULL,
                                        stderr=errors, start_new_session=True)
            processes.append(receiver)
            sender.stdout.close()
            deadline = time.monotonic() + timeout
            while any(p.poll() is None for p in processes):
                readable, _, _ = select.select([sys.stdin], [], [], 0.2)
                if readable and not os.read(sys.stdin.fileno(), 1):
                    raise RuntimeError('Readiness transfer cancelled')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Readiness transfer exceeded its time limit')
                if any(p.poll() not in (None, 0) for p in processes):
                    break
            if any(p.poll() != 0 for p in processes):
                errors.seek(0)
                raise RuntimeError('Readiness transfer failed: ' + errors.read().decode(errors='replace')[-4000:])
        finally:
            for process in processes:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()


if __name__ == '__main__':
    transfer(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), float(sys.argv[4]))
