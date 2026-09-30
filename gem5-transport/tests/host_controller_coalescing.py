"""Withhold guest polls and check the newest clock and ordered scan delivery."""
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading


def read_exact(connection, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        assert chunk, 'host disconnected before sending the packet'
        chunks.extend(chunk)
    return bytes(chunks)


def check(binary):
    with tempfile.TemporaryDirectory(prefix='chimaera-coalescing-') as directory:
        root = Path(directory)
        timing, g2h, h2g = [str(root / name) for name in ('timing', 'g2h', 'h2g')]
        errors = []
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(timing)
            server.listen()
            server.settimeout(10)

            def serve():
                tick = 0
                try:
                    while True:
                        connection, _ = server.accept()
                        with connection, connection.makefile() as stream:
                            command = stream.readline().strip()
                            if command == 'QUIT':
                                connection.sendall(b'BYE\n')
                                return
                            assert command.startswith('STEP_TICKS '), command
                            end = tick + int(command.split()[1])
                            connection.sendall(f'OK {tick} {end}\n'.encode())
                            tick = end
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            process = subprocess.Popen([binary, timing, g2h, h2g], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                def line(expected):
                    assert select.select([process.stdout], [], [], 10)[0], 'host driver timed out'
                    actual = process.stdout.readline().strip()
                    assert actual == expected, (actual, process.poll(),
                        process.stderr.read() if process.poll() is not None else 'driver still running')

                def transfer(path, data=None, size=0):
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.settimeout(3)
                        connection.connect(path)
                        if data is not None:
                            connection.sendall(struct.pack('=Q', len(data)) + data)
                            return b''
                        return read_exact(connection, size)

                def poll():
                    packet = struct.pack('>5Q', 0x4348494D43545231, 1, 0, 0, 0)
                    transfer(g2h, struct.pack('>Q', len(packet)))
                    transfer(g2h, packet)
                    size, = struct.unpack('>Q', transfer(h2g, size=8))
                    data = transfer(h2g, size=size)
                    magic, kind, epoch, duration, count = struct.unpack('>5Q', data[:40])
                    assert magic == 0x4348494D43545231 and kind == 2 and epoch >= 1100
                    assert duration == 100000
                    messages, offset = [], 40
                    for _ in range(count):
                        length, = struct.unpack('>Q', data[offset:offset + 8])
                        offset += 8
                        messages.append(data[offset:offset + length].decode())
                        offset += length
                    assert offset == len(data)
                    return messages

                line('READY')
                messages = poll()
                assert messages == ['s:10', 's:1000', 'c:1099'], messages
                process.stdin.write('step\n')
                process.stdin.flush()
                line('STEPPED')
                assert poll() == []
                process.stdin.write('quit\n')
                process.stdin.flush()
                assert process.wait(timeout=5) == 0, process.stderr.read()
                thread.join(timeout=2)
                assert not thread.is_alive() and not errors, errors
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    print('1100 stalled-guest intervals advanced; newest clock and both ordered scans delivered')


if __name__ == '__main__':
    check(sys.argv[1])
