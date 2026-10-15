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
if role != 'source' and 'inspect' in command:
    if not marker.exists():
        sys.exit(1)
    print(marker.read_text())
elif role == 'source':
    assert 'docker image tag ' + image + ' chimaera:transfer-' in command
    assert 'docker image save chimaera:transfer-' in command
    if mode == 'block':
        time.sleep(60)
    if mode == 'slow':
        print('CHIMAERA_TRANSFER_SIZE', 4000000, file=sys.stderr, flush=True)
        print('CHIMAERA_TRANSFER_BYTES', 1000000, file=sys.stderr, flush=True)
        sys.stdout.buffer.write(b'archive-content' * 32768)
        sys.stdout.buffer.flush()
        time.sleep(1.3)
        print('CHIMAERA_TRANSFER_BYTES', 2000000, file=sys.stderr, flush=True)
        sys.stdout.buffer.write(b'archive-content' * 32768)
        sys.stdout.buffer.flush()
        time.sleep(1.1)
    else:
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
    print('Loaded image: chimaera:transfer-' + 'a' * 64, flush=True)
    if mode == 'missing_after_load':
        sys.exit(0)
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
        with pytest.raises(ValueError, match='Invalid Docker image ID'):
            SSH().copy_image({}, {}, 'mutable:tag')
        execute.assert_not_called()


def test_transfer_reports_bytes_and_load_verification_phases(sdk_stream):
    configure, _ = sdk_stream
    updates = []
    configure().copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                           progress=updates.append)
    assert updates[0]['phase'] == 'Checking destination cache'
    assert {'Streaming', 'Loading image', 'Verifying image', 'Complete'} <= {u['phase'] for u in updates}
    assert updates[-1]['bytes_sent'] == len(b'archive-content') * 65536
    assert updates[-1]['bytes_per_second'] > 0
    assert [u['bytes_sent'] for u in updates] == sorted(u['bytes_sent'] for u in updates)


def test_missing_import_reports_destination_and_docker_load_output(sdk_stream):
    configure, _ = sdk_stream
    with pytest.raises(RuntimeError, match='Destination image verification failed.*Loaded image: chimaera:transfer-'):
        configure('missing_after_load').copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID)


def test_transfer_eta_waits_for_samples_and_excludes_loading(sdk_stream):
    configure, _ = sdk_stream
    updates = []
    configure('slow').copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                                 progress=updates.append)
    assert updates[0]['eta_seconds'] is None
    estimates = [u for u in updates if u['phase'] == 'Streaming' and u['eta_seconds'] is not None]
    assert estimates
    assert all(u['eta_seconds'] > 0 for u in estimates)
    assert all('excludes loading' in u['eta_basis'] for u in estimates)
    assert all(u['eta_seconds'] is None for u in updates if u['phase'] in {'Loading image', 'Verifying image'})
    assert updates[-1]['eta_seconds'] == 0


def test_transfer_eta_is_unknown_without_size_metadata(sdk_stream):
    configure, _ = sdk_stream
    updates = []
    configure().copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                           progress=updates.append)
    assert all(u['eta_seconds'] is None for u in updates if u['phase'] == 'Streaming')


def test_metered_export_preserves_archive_through_real_gzip_pipeline(tmp_path, monkeypatch):
    import os
    docker = tmp_path / 'docker'
    marker = tmp_path / 'loaded'
    docker.write_text("""#!/usr/bin/env python3
import os,pathlib,sys
args=sys.argv[1:]
marker=pathlib.Path(os.environ['LOAD_MARKER'])
image='sha256:'+'a'*64
if args[:2]==['image','tag']:
    sys.exit(0)
if args[:2]==['image','inspect']:
    if 'CHIMAERA_TRANSFER_SIZE {{.Size}}' in args:
        print('CHIMAERA_TRANSFER_SIZE',len(b'archive-content'*65536))
    elif marker.exists():
        print(image)
    else:
        sys.exit(1)
elif args[:2]==['image','save']:
    sys.stdout.buffer.write(b'archive-content'*65536)
elif args[:2]==['image','load']:
    assert sys.stdin.buffer.read()==b'archive-content'*65536
    marker.write_text(image)
    print('Loaded image: chimaera:transfer-'+'a'*64)
else:
    sys.exit(2)
""")
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('LOAD_MARKER', str(marker))
    ssh = SSH()
    ssh.base = lambda machine: ['bash', '-c']
    updates = []
    assert ssh.copy_image({}, {}, SDK_ID, progress=updates.append) == SDK_ID
    assert marker.read_text() == SDK_ID
    assert updates[-1]['bytes_sent'] > 0
    assert updates[-1]['bytes_sent'] < len(b'archive-content' * 65536)
