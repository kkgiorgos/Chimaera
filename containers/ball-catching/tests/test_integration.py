"""Exercise portable exports and isolated worker/report handoff without booting gem5."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import types

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
ROOT = DIRECTORY.parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


configure = module('ball_configure', DIRECTORY / 'configure.py')
worker = module('ball_worker', DIRECTORY / 'worker.py')
compare = module('ball_compare', DIRECTORY / 'compare.py')
runner = module('generic_runner_for_ball', DIRECTORY.parent / 'run.py')


def test_export_freezes_the_exact_ten_case_matrix_and_portable_mounts(tmp_path):
    disk, kernel = tmp_path / 'disk.img', tmp_path / 'kernel'
    disk.write_bytes(b'guest')
    kernel.write_bytes(b'kernel')
    inputs = tmp_path / 'inputs'
    config = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    configure.configure(config, inputs, 'sha256:fixed-image', disk, kernel)
    exported = runner.load(inputs / 'runner.json')
    assert len(exported['jobs']) == 10
    assert exported['image'] == 'sha256:fixed-image'
    assert exported['devices'] == ['/dev/kvm']
    assert all(Path(m['source']).exists() for m in exported['mounts'])
    assert json.loads((inputs / 'experiment.json').read_text())['ramulator_config'] == '/assets/inputs/memory.json'
    record = json.loads((inputs / 'inputs.json').read_text())
    for path, expected in record['sha256'].items():
        assert worker.digest(inputs / path) == expected
    points = json.loads((inputs / 'plan.json').read_text())['points']
    assert [p['pattern'] for p in points] == ['none'] + ['stream']*3 + ['stride']*3 + ['random']*3
    fake_device = tmp_path / 'kvm'
    fake_device.touch()
    exported['devices'] = [str(fake_device)]
    commands = [runner.command(exported, job, tmp_path / job['name'], 'sha256:fixed-image', job['name'])
                for job in exported['jobs'][:2]]
    for command in commands:
        assert command[command.index('--network')+1] == 'none'
        assert command[command.index('--ipc')+1] == 'private'
        assert '--read-only' in command
        assert 'readonly' in ' '.join(command)
    assert commands[0][-1] == 'case-000' and commands[1][-1] == 'case-001'
    assert commands[0] != commands[1]
    assert not (inputs / 'prepared').exists()


def inputs_record(tmp_path):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'inputs.json').write_text(json.dumps(dict(sha256={}, source_revision='snapshot')))
    return inputs


def test_preparation_only_calibrates_memory_and_packages_guest(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    (inputs / 'experiment.json').write_text(json.dumps(dict(patterns=['stream', 'random'])))
    commands = []
    monkeypatch.setattr(worker.subprocess, 'run', lambda command, **kw: commands.append(command))
    worker.prepare(inputs, tmp_path / 'prepared')
    assert '--calibrate-only' in commands[0]
    assert 'check_aggressor_rates.py' in commands[1][1]
    assert '--prepare-only' in commands[2] and '--resume' in commands[2]
    assert all('--source-revision' in c for c in (commands[0], commands[2]))
    assert all('--case' not in c for c in commands)


def test_producer_prepares_readiness_once_after_packaging(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    (inputs / 'experiment.json').write_text(json.dumps(dict(patterns=['stream'])))
    commands = []
    monkeypatch.setattr(worker.subprocess, 'run', lambda command, **kw: commands.append(command))
    worker.prepare(inputs, tmp_path / 'prepared', checkpoint_create=True)
    assert '--checkpoint-create' in commands[-1]
    assert '--resume' in commands[-1]
    assert sum('--checkpoint-create' in command for command in commands) == 1
    assert all('--case' not in command for command in commands)


@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize('existing', [None, 'unexpected.txt', 'calibration'])
def test_preparation_progress_passes_real_suite_initialization(tmp_path, monkeypatch, shared, existing):
    import report
    import run_memory_suite as suite

    inputs = inputs_record(tmp_path)
    config_path = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    data = json.loads(config_path.read_text())
    data['ramulator_config'] = str((config_path.parent / data['ramulator_config']).resolve())
    (inputs / 'experiment.json').write_text(json.dumps(data))
    artifacts = tmp_path / 'gem5'
    for name in ('build/X86/gem5.opt', 'resources/x86-ubuntu-22.04-ros-humble.img',
                 'resources/x86-linux-kernel-5.15.180'):
        path = artifacts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'artifact')
    output = tmp_path / 'prepared'
    output.mkdir()
    if existing == 'calibration':
        (output / existing).mkdir()
    elif existing:
        (output / existing).write_text('previous output')
    calibrated = []
    def calibrate(directory, config, gem5, source):
        calibrated.append(source)
        assert json.loads((directory / 'preparation.json').read_text())['phase'] == (
            'Validating shared memory calibration' if shared else 'Calibrating memory')
        return {'stream': {'sustainable_gbps': 40}}
    monkeypatch.setattr(suite, 'calibrate', calibrate)
    monkeypatch.setattr(suite, 'execution_fingerprint', lambda: 'installed-artifacts')
    monkeypatch.setattr(report, 'create_dashboard', lambda *args: None)
    def execute(command, **kwargs):
        if '--calibrate-only' in command:
            argv = command[1:].copy()
            argv[argv.index('--gem5-root') + 1] = str(artifacts)
            argv[argv.index('--image') + 1] = str(artifacts / 'resources/x86-ubuntu-22.04-ros-humble.img')
            argv[argv.index('--kernel') + 1] = str(artifacts / 'resources/x86-linux-kernel-5.15.180')
            with monkeypatch.context() as context:
                context.setattr(sys, 'argv', argv)
                assert suite.main() == 0
        else:
            assert (output / 'experiment_manifest.json').is_file()
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(worker.subprocess, 'run', execute)
    source = tmp_path / 'shared-calibration' if shared else None
    if existing:
        with pytest.raises(SystemExit) as error:
            worker.prepare(inputs, output, calibration_from=source)
        assert error.value.code == 2
        assert not calibrated
        assert not (output / 'experiment_manifest.json').exists()
    else:
        worker.prepare(inputs, output, calibration_from=source)
        assert calibrated == [source]
        manifest = json.loads((output / 'experiment_manifest.json').read_text())
        assert manifest['calibration']['stream']['sustainable_gbps'] == 40
        assert json.loads((output / 'preparation.json').read_text())['phase'] == 'Prepared inputs ready'


def test_missing_required_checkpoint_never_falls_back_to_kvm(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    monkeypatch.setenv('CHIMAERA_CHECKPOINT_ROOT', str(tmp_path / 'missing'))
    with pytest.raises(ValueError, match='Required readiness checkpoints are missing'):
        worker.run_case(inputs, tmp_path / 'out', 'case-000')
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('cached_bundle', [False, True])
def test_imported_bundle_prepares_routes_for_case_start(tmp_path, monkeypatch, cached_bundle):
    import types
    import report
    import run_memory_suite as suite

    inputs = inputs_record(tmp_path)
    config = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    artifacts = tmp_path / 'gem5'
    for name in ('build/X86/gem5.opt', 'resources/x86-ubuntu-22.04-ros-humble.img',
                 'resources/x86-linux-kernel-5.15.180'):
        path = artifacts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'artifact')
    source = tmp_path / 'producer-guest.tar.gz'
    source.write_bytes(b'exact producer bundle')
    prepared = inputs / 'prepared'
    prepared.mkdir()
    if cached_bundle:
        (prepared / 'guest.tar.gz').write_bytes(source.read_bytes())
        # A prior calibration creates the manifest before staging the bundle.
        data = suite.load_config(config)
        (prepared / 'experiment_manifest.json').write_text(json.dumps(dict(
            configuration_sha256=hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            configuration=data, mode='gem5', points=suite.plan(data))))
    package_index = types.ModuleType('ament_index_python.packages')
    package_index.get_package_prefix = lambda package: str(tmp_path / package)
    monkeypatch.setitem(sys.modules, 'ament_index_python.packages', package_index)
    monkeypatch.setattr(report, 'create_dashboard', lambda *args: None)
    monkeypatch.setattr(suite, 'execution_fingerprint', lambda: 'installed-artifacts')
    def unexpected_bundle_build(*args):
        pytest.fail('An imported bundle must not be rebuilt')
    monkeypatch.setattr(suite, 'prepare_bundle', unexpected_bundle_build)
    monkeypatch.setattr(sys, 'argv', ['run_memory_suite.py', '--config', str(config),
        '--output', str(prepared), '--gem5-root', str(artifacts), '--prepare-only',
        '--bundle-from', str(source), '--source-revision', 'snapshot',
        *(['--resume'] if cached_bundle else [])])
    assert suite.main() == 0
    assert json.loads((prepared / 'routes.json').read_text()) == suite.routes()
    assert (prepared / 'guest.tar.gz').read_bytes() == source.read_bytes()
    (prepared / 'calibration').mkdir()
    calls = []
    monkeypatch.setattr(worker.subprocess, 'call', lambda command: calls.append(command) or 0)
    output = tmp_path / 'case-output'
    assert worker.run_case(inputs, output, 'case-000') == 0
    assert json.loads((output / 'routes.json').read_text()) == suite.routes()
    assert calls[0][-3:] == ['--resume', '--case', 'case-000']


def test_worker_reuses_bundle_and_shared_calibration_then_resumes_one_case(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    prepared = inputs / 'prepared'
    prepared.mkdir()
    (prepared / 'calibration').mkdir()
    (prepared / 'guest.tar.gz').write_bytes(b'guest bundle')
    (prepared / 'routes.json').write_text('{}')
    (prepared / 'experiment_manifest.json').write_text(json.dumps(dict(points=[dict(id='case-002')])) )
    output = tmp_path / 'output'
    calls = []
    monkeypatch.setattr(worker.subprocess, 'call', lambda command: calls.append(command) or 0)
    assert worker.run_case(inputs, output, 'case-002') == 0
    assert (output / 'guest.tar.gz').read_bytes() == b'guest bundle'
    assert (output / 'calibration').is_symlink()
    assert calls[0][-3:] == ['--resume', '--case', 'case-002']
    assert worker.run_case(inputs, output, 'case-002') == 0
    assert len(calls) == 2
    with pytest.raises(ValueError, match='Unknown case'):
        worker.run_case(inputs, tmp_path / 'invalid', 'case-999')
    assert not (tmp_path / 'invalid').exists()


def test_changed_frozen_inputs_fail_before_execution(tmp_path):
    inputs = inputs_record(tmp_path)
    (inputs / 'experiment.json').write_text('{}')
    (inputs / 'inputs.json').write_text(json.dumps(dict(sha256={'experiment.json': 'wrong'}, source_revision='snapshot')))
    with pytest.raises(ValueError, match='Frozen input changed'):
        worker.common(inputs, tmp_path / 'out')


def test_aggregate_keeps_baseline_completed_and_failed_cases(tmp_path, monkeypatch):
    inputs = inputs_record(tmp_path)
    points = [dict(id=f'case-{i:03d}', status='pending', trials=[]) for i in range(3)]
    (inputs / 'plan.json').write_text(json.dumps(dict(points=points)))
    output = tmp_path / 'results'
    for i, status in enumerate(('complete', 'infrastructure_failure')):
        suite = output / 'jobs' / f'case-{i:03d}' / 'suite'
        suite.mkdir(parents=True)
        trials = ['case-000-attempt-001'] if i == 0 else []
        (suite / 'experiment_manifest.json').write_text(json.dumps(dict(points=[dict(
            id=f'case-{i:03d}', status=status, trials=trials, reason='saved failure' if i else '')])))
    captured = []
    monkeypatch.setattr(compare, 'create_dashboard', lambda *args: captured.append(args))
    compare.collect(inputs, output)
    directories, destination, reported = captured[0]
    assert len(directories) == 1 and directories[0].name == 'case-000-attempt-001'
    assert [p['status'] for p in reported] == ['complete', 'infrastructure_failure', 'pending']
    assert reported[1]['reason'] == 'saved failure'
    assert destination == output / 'comparison/dashboard.html'


@pytest.mark.parametrize('cpus,memory', [(0, '12g'), (float('nan'), '12g'), (4, 'none')])
def test_bad_resource_limits_fail_before_writing_inputs(tmp_path, cpus, memory):
    with pytest.raises(ValueError, match='resource limits'):
        configure.configure(Path('unused'), tmp_path / 'inputs', 'image', Path('disk'), Path('kernel'), cpus, memory)
    assert not (tmp_path / 'inputs').exists()


def test_prebuilt_sdk_export_hashes_required_artifacts(tmp_path):
    build = module('generic_build_for_ball', DIRECTORY.parent / 'build.py')
    root = tmp_path / 'gem5'
    for name in ('build/X86/gem5.opt', 'util/m5/build/x86/out/m5', 'util/m5/build/x86/out/libm5.a',
                 'ext/ramulator2/ramulator2/libramulator.so', 'src/mem/ramulator2/LICENSE',
                 'ext/ramulator2/configs/memory.json'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    output = tmp_path / 'export'
    output.mkdir()
    build.stage_gem5(root, output)
    files = json.loads((output / 'prebuilt-gem5.json').read_text())['files']
    assert len(files) == 6
    for path, digest in files.items():
        assert build._digest(output / path) == digest


def test_dma_export_freezes_driver_and_keeps_kvm_for_cases(tmp_path):
    disk, kernel, driver = [tmp_path / name for name in ('disk.img', 'kernel', 'driver.ko')]
    for path in (disk, kernel, driver):
        path.write_bytes(path.name.encode())
    source = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    data = json.loads(source.read_text())
    data['ramulator_config'] = str((source.parent / data['ramulator_config']).resolve())
    data['fixed'].update(transport_backend='dma', dma_module=driver.name)
    config = tmp_path / 'dma.json'
    config.write_text(json.dumps(data))
    inputs = tmp_path / 'inputs'
    configure.configure(config, inputs, 'image', disk, kernel)
    exported = json.loads((inputs / 'experiment.json').read_text())
    assert exported['fixed']['transport_backend'] == 'dma'
    assert exported['fixed']['dma_module'] == '/assets/inputs/chimaera_dma.ko'
    assert (inputs / 'chimaera_dma.ko').read_bytes() == driver.read_bytes()
    record = json.loads((inputs / 'inputs.json').read_text())
    assert record['sha256']['chimaera_dma.ko'] == worker.digest(driver)
    assert json.loads((inputs / 'runner.json').read_text())['devices'] == ['/dev/kvm']


@pytest.mark.parametrize('include_dma', [False, True])
def test_m5ops_export_survives_relocation_and_removal_of_original_modules(tmp_path, include_dma):
    disk, kernel, driver = [tmp_path / name for name in ('disk.img', 'kernel', 'notification.ko')]
    for path in (disk, kernel, driver):
        path.write_bytes(path.name.encode())
    source = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    data = json.loads(source.read_text())
    data['ramulator_config'] = str((source.parent / data['ramulator_config']).resolve())
    data['fixed'].update(transport_backend='m5ops', middleware_transport='fastdds', m5_module=driver.name)
    modules = {'m5_module': (driver, 'chimaera_m5.ko')}
    if include_dma:
        dma = tmp_path / 'dma.ko'
        dma.write_bytes(b'optional DMA driver')
        data['fixed']['dma_module'] = dma.name
        modules['dma_module'] = (dma, 'chimaera_dma.ko')
    config = tmp_path / 'm5ops.json'
    config.write_text(json.dumps(data))
    inputs = tmp_path / 'inputs'
    configure.configure(config, inputs, 'sha256:application', disk, kernel)
    relocated = inputs.rename(tmp_path / 'relocated-inputs')
    exported = json.loads((relocated / 'experiment.json').read_text())
    record = json.loads((relocated / 'inputs.json').read_text())
    assert exported['fixed']['transport_backend'] == 'm5ops'
    assert exported['fixed']['middleware_transport'] == 'fastdds'
    for key, (original, frozen) in modules.items():
        assert exported['fixed'][key] == '/assets/inputs/' + frozen
        assert (relocated / frozen).read_bytes() == original.read_bytes()
        assert record['sha256'][frozen] == worker.digest(original)
        original.unlink()
    helper = 'app/ros/ball_catching_ws/scripts/wait_dds_graph.py'
    assert record['sha256'][helper] == worker.digest(configure.SCRIPTS / 'wait_dds_graph.py')
    command = worker.common(relocated, tmp_path / 'output')
    assert command[command.index('--config') + 1] == str(relocated / 'experiment.json')
    assert json.loads((relocated / 'runner.json').read_text())['devices'] == ['/dev/kvm']
    (relocated / 'chimaera_m5.ko').write_bytes(b'replaced notification ABI')
    with pytest.raises(ValueError, match='Frozen input changed: chimaera_m5.ko'):
        worker.common(relocated, tmp_path / 'output')


def test_direct_m5ops_guest_bundle_contains_runtime_and_graph_admission_helper(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge'))
    from chimaera_ros import dds
    from chimaera_ros.manifest import load
    from chimaera_ros.runner import plan
    import run_memory_suite as suite

    sdk, app = tmp_path / 'sdk', tmp_path / 'application'
    libraries = ('libchimaera_gem5_clock_addr.so', 'librmw_fastrtps_cpp.so',
                 'librmw_fastrtps_shared_cpp.so', 'libchimaera_dds_transport.so')
    for name in [*('lib/' + name for name in libraries), 'lib/chimaera_ros_bridge/guest_bridge']:
        path = sdk / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    binary = app / 'lib/ball_catching_robot/perception'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'\x7fELFmock robot')
    packages = types.ModuleType('ament_index_python.packages')
    packages.get_package_prefix = lambda name: str(app if name == 'ball_catching_robot' else sdk)
    monkeypatch.setitem(sys.modules, 'ament_index_python', types.ModuleType('ament_index_python'))
    monkeypatch.setitem(sys.modules, 'ament_index_python.packages', packages)
    selected_plugins = []
    def middleware(path):
        selected_plugins.append(path)
        return types.SimpleNamespace(chimaera_rmw_transport_abi=lambda: 1)
    monkeypatch.setattr(dds.ctypes, 'CDLL', middleware)
    dependencies = ''.join(f'{name} => {sdk / "lib" / name} (0x1)\n' for name in libraries[2:])
    dependencies += 'libc.so.6 => /lib/libc.so.6 (0x2)\n'
    def ldd(command, **kwargs):
        assert command[0] == 'ldd'
        return subprocess.CompletedProcess(command, 0, stdout=dependencies)
    monkeypatch.setattr(suite.subprocess, 'run', ldd)
    source = ROOT / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
    data = suite.load_config(source)
    driver = tmp_path / 'chimaera_m5.ko'
    driver.write_bytes(b'kernel-matched notification module')
    data['fixed'].update(transport_backend='m5ops', middleware_transport='fastdds', m5_module=str(driver))
    output = tmp_path / 'prepared'
    output.mkdir()
    bundle = suite.prepare_bundle(output, data, suite.plan(data))
    with tarfile.open(bundle) as archive:
        root = suite.GUEST_ROOT.lstrip('/')
        for name in libraries:
            assert archive.extractfile(root + '/lib/' + name).read() == (sdk / 'lib' / name).read_bytes()
        assert archive.extractfile(root + '/runtime/chimaera_m5.ko').read() == driver.read_bytes()
        assert archive.extractfile(root + '/runtime/wait_dds_graph.py').read() == (
            configure.SCRIPTS / 'wait_dds_graph.py').read_bytes()
        runtime = json.load(archive.extractfile(root + '/runtime-libraries.json'))
        for name in libraries[1:]:
            assert runtime[name]['sha256'] == worker.digest(sdk / 'lib' / name)
        assert root + '/lib/libc.so.6' not in archive.getnames()
    assert selected_plugins == [str(sdk / 'lib/librmw_fastrtps_cpp.so')]
    guest = plan(load(output / 'staging-session.json'), 'guest', '/bridge')
    assert guest['env']['CHIMAERA_DDS_BACKEND'] == 'm5ops'
    assert guest['env']['CHIMAERA_M5_DEVICE'] == '/dev/chimaera-m5'
    coordinator = guest['processes'][0]['command']
    assert coordinator[:3] == ['env', '-u', 'CHIMAERA_DDS_SOCKET']
    assert 'm5_module:=' + suite.GUEST_ROOT + '/runtime/chimaera_m5.ko' in coordinator
    assert 'application_ready_file:=/tmp/chimaera-ball-dds-ready' in coordinator
    assert next(p for p in guest['processes'] if p['name'] == 'dds_readiness')['command'] == [
        'python3', suite.GUEST_ROOT + '/runtime/wait_dds_graph.py',
        '--ready-file', '/tmp/chimaera-ball-dds-ready', '--timeout', str(data['fixed']['startup_timeout_s'])]
