"""Exercise automatic receive with stdin idle and a partially typed command."""
import os
import selectors
import socket
import subprocess
import sys
import tempfile
import threading
import time


def run(executable):
    with tempfile.TemporaryDirectory(prefix="chimaera-console-") as directory:
        path = directory + "/channels.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        server.bind(path)
        server.listen()
        server.settimeout(0.1)
        stop = threading.Event()
        partial = threading.Event()
        sent = []
        failures = []

        def serve():
            replies = [b"automatic", b""]
            try:
                while not stop.is_set():
                    try:
                        peer, _ = server.accept()
                    except socket.timeout:
                        continue
                    with peer:
                        peer.settimeout(2)
                        request = peer.recv(4101)
                        assert request[1:5] == b"\x00\x00\x00\x01"
                        if request[0] == 1:
                            sent.append(request[5:])
                            peer.send(b"\x00")
                        elif partial.is_set() and replies:
                            peer.send(b"\x00" + replies.pop(0))
                        else:
                            peer.send(b"\x01")
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=serve)
        worker.start()
        process = subprocess.Popen(
            [executable, "--channel", "1", path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            # No newline: receiving must continue without waiting for getline.
            process.stdin.write(b"send par")
            process.stdin.flush()
            partial.set()
            output = b""
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 5
                while b"Received 0 bytes: \n" not in output:
                    assert time.monotonic() < deadline, output
                    if selector.select(0.1):
                        data = os.read(process.stdout.fileno(), 4096)
                        assert data, process.stderr.read()
                        output += data
            assert b"Received 9 bytes: automatic\n" in output, output
            process.stdin.write(b"tial\nsend\nquit\n")
            process.stdin.flush()
            tail, errors = process.communicate(timeout=5)
            assert process.returncode == 0, errors
            assert sent == [b"partial", b""], sent
            assert (output + tail).count(b"Queued\n") == 2
            assert not failures, failures
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            stop.set()
            worker.join(timeout=3)
            server.close()


for executable in sys.argv[1:]:
    run(executable)
