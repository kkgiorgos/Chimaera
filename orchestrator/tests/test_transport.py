import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from transport import SSH


def test_deleting_experiment_interrupts_ssh_fetch_and_reaps_process(tmp_path):
    ssh = SSH()
    cancelled = threading.Event()
    processes = []
    real_popen = subprocess.Popen

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        with patch.object(ssh, 'base', return_value=[sys.executable, '-c', 'import time; time.sleep(60)']), \
             patch('transport.subprocess.Popen', side_effect=start):
            with pytest.raises(RuntimeError, match='cancelled'):
                ssh.fetch({}, '/remote', tmp_path / 'archive', cancel=cancelled)
    finally:
        timer.cancel()
    assert len(processes) == 1
    assert processes[0].poll() is not None
    # Input bundles should not be downloaded again with each case's results.
    assert '--exclude=./suite/guest.tar.gz' in processes[0].args[-1]


SDK_ID = 'sha256:' + 'a' * 64


@pytest.fixture
def sdk_stream(tmp_path):
    script = tmp_path / 'peer.py'
    marker = tmp_path / 'loaded'
    script.write_text('''import pathlib,sys,time
role, marker, mode, command = sys.argv[1:]
marker = pathlib.Path(marker)
image = 'sha256:' + 'a' * 64
if 'inspect' in command:
    if not marker.exists():
        sys.exit(1)
    print(marker.read_text())
elif role == 'source':
    if mode == 'block':
        time.sleep(60)
    sys.stdout.buffer.write(b'archive-content' * 65536)
    if mode == 'sender_failure':
        print('export failed', file=sys.stderr)
        sys.exit(7)
else:
    data = sys.stdin.buffer.read()
    if mode == 'receiver_failure':
        print('load failed', file=sys.stderr)
        sys.exit(9)
    assert data == b'archive-content' * 65536
    marker.write_text('sha256:' + 'b' * 64 if mode == 'wrong_identity' else image)
''')
    ssh = SSH()
    def configure(mode='success'):
        ssh.base = lambda machine: [sys.executable, str(script), machine['role'], str(marker), mode]
        return ssh
    return configure, marker


def test_sdk_stream_verifies_identity_and_skips_existing_image(sdk_stream):
    configure, marker = sdk_stream
    ssh = configure()
    assert ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID) == SDK_ID
    assert marker.read_text() == SDK_ID
    with patch('transport.subprocess.run', return_value=subprocess.CompletedProcess([], 0, SDK_ID)), \
         patch('transport.subprocess.Popen') as stream:
        assert ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID) == SDK_ID
        stream.assert_not_called()


@pytest.mark.parametrize('mode,message', [('sender_failure', 'export failed'),
                                         ('receiver_failure', 'load failed'),
                                         ('wrong_identity', 'identity differs')])
def test_sdk_stream_rejects_failed_export_load_or_wrong_image(sdk_stream, mode, message):
    configure, _ = sdk_stream
    with pytest.raises((RuntimeError, ValueError), match=message):
        configure(mode).copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID)


@pytest.mark.parametrize('cancel', [False, True])
def test_sdk_stream_cancellation_and_timeout_reap_both_processes(sdk_stream, cancel):
    configure, _ = sdk_stream
    ssh = configure('block')
    cancelled = threading.Event()
    processes = []
    real_popen = subprocess.Popen
    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    timer = threading.Timer(0.2, cancelled.set)
    if cancel:
        timer.start()
    try:
        with patch('transport.subprocess.Popen', side_effect=start):
            with pytest.raises((RuntimeError, TimeoutError), match='cancelled|time limit'):
                ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                               cancel=cancelled, timeout=5 if cancel else 0.1)
    finally:
        timer.cancel()
    # The initial cache probe and both streaming subprocesses have exited.
    assert len(processes) == 3
    assert all(process.poll() is not None for process in processes)


def test_sdk_transfer_requires_immutable_image_identity():
    with patch('transport.subprocess.run') as execute:
        with pytest.raises(ValueError, match='Invalid SDK image ID'):
            SSH().copy_image({}, {}, 'mutable:tag')
        execute.assert_not_called()
