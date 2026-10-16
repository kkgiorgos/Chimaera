import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from transport import SSH


@pytest.mark.parametrize('calibration_present', [False, True])
def test_case_collection_skips_container_only_calibration_link(tmp_path, calibration_present):
    import shlex
    from controller import safe_extract

    output = tmp_path / 'attempt'
    suite = output / 'suite'
    suite.mkdir(parents=True)
    target = tmp_path / 'worker-assets/calibration'
    if calibration_present:
        target.mkdir(parents=True)
        (target / 'calibration.json').write_text('shared calibration')
    (suite / 'calibration').symlink_to(target, target_is_directory=True)
    (suite / 'guest.tar.gz').write_bytes(b'large guest input')
    (suite / 'result.json').write_text('{"success": false}')
    (output / 'worker.log').write_text('completed')
    ssh = SSH()
    ssh.base = lambda machine: ['bash', '-c']
    real_popen = subprocess.Popen

    def local_tar(argv, **kwargs):
        command = shlex.split(argv[-1])
        assert command[:4] == ['docker', 'exec', 'chimaera-supervisor', 'tar']
        return real_popen(command[3:], **kwargs)

    archive = tmp_path / 'results.tar.gz'
    with patch('transport.subprocess.Popen', side_effect=local_tar):
        ssh.fetch({}, str(output), archive)
    extracted = tmp_path / 'extracted'
    safe_extract(archive, extracted)
    assert (extracted / 'suite/result.json').read_text() == '{"success": false}'
    assert (extracted / 'worker.log').read_text() == 'completed'
    assert not (extracted / 'suite/calibration').exists()
    assert not (extracted / 'suite/guest.tar.gz').exists()


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
def sdk_stream(tmp_path, monkeypatch):
    import os
    script = tmp_path / 'peer.py'
    marker = tmp_path / 'loaded'
    script.write_text('''import gzip,json,pathlib,random,sys
marker, mode, command = sys.argv[1:]
marker = pathlib.Path(marker)
image = 'sha256:' + 'a' * 64
if 'inspect' in command:
    if not marker.exists():
        sys.exit(1)
    if '{{json .}}' in command:
        content = dict(Config={'Env': ['SDK=1'], 'Cmd': ['/bin/bash']},
                       RootFS={'Type': 'layers', 'Layers': ['sha256:' + 'c' * 64]},
                       Architecture='amd64', Os='linux', Size=999)
        if mode == 'different_config':
            content['Config']['Env'] = ['SDK=2']
        elif mode == 'different_layers':
            content['RootFS']['Layers'] = ['sha256:' + 'd' * 64]
        elif mode == 'different_platform':
            content['Architecture'] = 'arm64'
        elif mode == 'missing_content':
            del content['RootFS']
        print(json.dumps(content))
    else:
        print(marker.read_text())
else:
    data = gzip.decompress(sys.stdin.buffer.read())
    if mode == 'receiver_failure':
        print('load failed', file=sys.stderr)
        sys.exit(9)
    expected = (random.Random(0).randbytes(14 * 32768) * 2 if mode == 'slow' else b'archive-content' * 65536)
    assert data == expected
    print('Loaded image: chimaera:transfer-' + 'a' * 64, flush=True)
    if mode == 'missing_after_load':
        sys.exit(0)
    marker.write_text('sha256:' + 'b' * 64 if mode in {
        'wrong_identity', 'cross_store', 'different_config', 'different_layers',
        'different_platform', 'missing_content'} else image)
''')
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json,os,random,sys,time
args=sys.argv[1:]
mode=os.environ['TRANSFER_TEST_MODE']
if args[:2] == ['image', 'tag']:
    assert args[2] == 'sha256:' + 'a' * 64
    assert args[3] == 'chimaera:transfer-' + 'a' * 64
elif args[:2] == ['image', 'inspect']:
    if '{{json .}}' in args:
        if mode == 'wrong_identity':
            sys.exit(1)
        print(json.dumps(dict(Config={'Env': ['SDK=1'], 'Cmd': ['/bin/bash']},
                              RootFS={'Type': 'layers', 'Layers': ['sha256:' + 'c' * 64]},
                              Architecture='amd64', Os='linux', Size=123)))
    elif mode != 'no_metadata':
        print('CHIMAERA_TRANSFER_SIZE', 4000000)
elif args[:2] == ['image', 'save']:
    assert args[2] == 'chimaera:transfer-' + 'a' * 64
    if mode == 'block':
        time.sleep(60)
    if mode == 'slow':
        sys.stdout.buffer.write(random.Random(0).randbytes(14 * 32768))
        sys.stdout.buffer.flush()
        time.sleep(1.3)
    sys.stdout.buffer.write(random.Random(0).randbytes(14 * 32768) if mode == 'slow' else b'archive-content' * 65536)
    sys.stdout.buffer.flush()
    if mode == 'slow':
        time.sleep(1.1)
    if mode == 'sender_failure':
        print('export failed', file=sys.stderr)
        sys.exit(7)
else:
    sys.exit(2)
''')
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    ssh = SSH()
    def configure(mode='success'):
        monkeypatch.setenv('TRANSFER_TEST_MODE', mode)
        ssh.base = lambda machine: (['bash', '-c'] if machine['role'] == 'source' else
                                   [sys.executable, str(script), str(marker), mode])
        return ssh
    return configure, marker


def test_controller_launches_only_producer_and_receives_json_progress(sdk_stream):
    import json
    import os
    configure, _ = sdk_stream
    processes = []
    real_popen = subprocess.Popen

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append((process, kwargs))
        return process

    def progress(value):
        if value['phase'] == 'Complete':
            output = processes[-1][1]['stdout']
            messages = os.pread(output.fileno(), 65536, 0)
            assert all(isinstance(json.loads(line), dict) for line in messages.splitlines())

    with patch('transport.subprocess.Popen', side_effect=start):
        assert configure().copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                                      progress=progress) == SDK_ID
    # One destination cache probe, then one producer control connection. The
    # destination loader and both archive pipes exist only inside the producer.
    assert len(processes) == 2
    assert processes[1][0].args[:2] == ['bash', '-c']
    assert processes[1][1]['stdout'] != subprocess.PIPE


def test_producer_stops_when_control_connection_closes(sdk_stream):
    configure, _ = sdk_stream
    real_popen = subprocess.Popen
    processes = []

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        if process.args[:2] == ['bash', '-c']:
            process.stdin.close()
        return process

    with patch('transport.subprocess.Popen', side_effect=start):
        with pytest.raises(RuntimeError, match='Image transfer cancelled'):
            configure('block').copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID)
    assert all(process.poll() is not None for process in processes)


def test_sdk_stream_verifies_identity_and_skips_existing_image(sdk_stream):
    configure, marker = sdk_stream
    ssh = configure()
    assert ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID) == SDK_ID
    assert marker.read_text() == SDK_ID
    with patch('transport.subprocess.run', return_value=subprocess.CompletedProcess([], 0, SDK_ID)), \
         patch('transport.subprocess.Popen') as stream:
        assert ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID) == SDK_ID
        stream.assert_not_called()


def test_cross_store_transfer_returns_verified_local_id_and_reuses_loaded_tag(sdk_stream):
    configure, marker = sdk_stream
    ssh = configure('cross_store')
    local_id = 'sha256:' + 'b' * 64
    updates = []
    assert ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                          progress=updates.append) == local_id
    assert marker.read_text() == local_id
    assert updates[-1]['image_id'] == local_id
    assert updates[-1]['phase'] == 'Complete'
    updates.clear()
    assert ssh.copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
                          progress=updates.append) == local_id
    assert updates[-1]['phase'] == 'Already cached'
    assert not any(u['phase'] == 'Streaming' for u in updates)


@pytest.mark.parametrize('mode', ['different_config', 'different_layers', 'different_platform', 'missing_content'])
def test_cross_store_transfer_rejects_incompatible_or_unverifiable_content(sdk_stream, mode):
    configure, _ = sdk_stream
    with pytest.raises(RuntimeError, match='identity differs'):
        configure(mode).copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID)


@pytest.mark.parametrize('mode,message', [('sender_failure', 'export failed'),
                                         ('receiver_failure', 'load failed'),
                                         ('wrong_identity', 'identity differs')])
def test_sdk_stream_rejects_failed_export_load_or_wrong_image(sdk_stream, mode, message):
    configure, _ = sdk_stream
    with pytest.raises((RuntimeError, ValueError), match=message):
        configure(mode).copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID)


@pytest.mark.parametrize('cancel', [False, True])
def test_sdk_stream_cancellation_and_timeout_reap_control_process(sdk_stream, cancel):
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
    # The controller starts only a cache probe and a producer control connection.
    assert len(processes) == 2
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
    assert 0 < updates[-1]['bytes_sent'] < len(b'archive-content') * 65536
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
    configure('no_metadata').copy_image({'role': 'source'}, {'role': 'destination'}, SDK_ID,
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
