"""Exercise queue bundles over the real mock controllers and Unix sockets."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def send(process, text):
    process.stdin.write(text.encode())
    process.stdin.flush()


def wait_for(path, text):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if text in path.read_text():
            return
        time.sleep(0.01)
    raise AssertionError(f"Missing {text!r}:\n{path.read_text()}")


def received(path, peer, step, channel):
    prefix = f"[step {step}][channel {channel}] Received from {peer} "
    return [line.split(prefix, 1)[1] for line in path.read_text().splitlines()
            if prefix in line]


with tempfile.TemporaryDirectory(prefix="cqm-") as directory:
    root = Path(directory)
    processes = []
    try:
        for name, binary in zip(("host", "guest"), sys.argv[1:]):
            with (root / name).open("wb") as output:
                processes.append(subprocess.Popen(
                    [binary, str(root / "socket")], stdin=subprocess.PIPE,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True))
        host, guest = processes
        wait_for(root / "host", "Startup step 0 complete.")
        send(host, "send 3 invalid\nsend 1 first\nsend 2 second channel\nsend 1\nsend 1 last\n")
        send(guest, "send 2 guest first\nsend 1\nsend 2 guest last\n")
        send(host, "step 100 1\n")
        wait_for(root / "host", "Interval 1 complete")
        assert received(root / "guest", "host", 1, 1) == [
            "(5 bytes): first", "(0 bytes): ", "(4 bytes): last"]
        assert received(root / "guest", "host", 1, 2) == ["(14 bytes): second channel"]
        assert received(root / "host", "guest", 1, 1) == ["(0 bytes): "]
        assert received(root / "host", "guest", 1, 2) == [
            "(11 bytes): guest first", "(10 bytes): guest last"]
        assert "Commands: send CHANNEL" in (root / "host").read_text()
        # Fill one channel; the other remains usable. Reject overflow explicitly.
        send(host, "".join(f"send 1 item{i}\n" for i in range(65)) + "send 2 independent\nstep 20 1\n")
        wait_for(root / "host", "Interval 2 complete")
        assert "Channel 1 full" in (root / "host").read_text()
        assert received(root / "guest", "host", 2, 1) == [
            f"({len('item' + str(i))} bytes): item{i}" for i in range(64)]
        assert received(root / "guest", "host", 2, 2) == ["(11 bytes): independent"]
        # A fresh snapshot accepts data again; an empty interval adds no messages.
        send(host, "send 1 retry\nstep 20 1\nstep 20 1\nquit\n")
        for process in processes:
            assert process.wait(timeout=10) == 0
        assert received(root / "guest", "host", 3, 1) == ["(5 bytes): retry"]
        for channel in (1, 2):
            assert received(root / "guest", "host", 4, channel) == []
            assert received(root / "host", "guest", 4, channel) == []
        assert not (root / "socket").exists()
    except Exception:
        for name in ("host", "guest"):
            print(f"{name}:\n{(root / name).read_text()}", file=sys.stderr)
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            process.stdin.close()
