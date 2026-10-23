import ctypes
import errno
import fcntl
import importlib.util
import json
from pathlib import Path
import runpy
import struct
import subprocess
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from common import atomic_json, digest


ROOT = Path(__file__).resolve().parents[2]


def recipe(name):
    spec = importlib.util.spec_from_file_location('kernel_' + name, ROOT / 'orchestrator/kernel' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('backend', ['dma', 'm5ops'])
@pytest.mark.parametrize('matching_release', [False, True])
def test_kernel_recipe_builds_selected_driver_against_full_pinned_tree(tmp_path, backend, matching_release):
    builder = recipe('build')
    paths = {name: tmp_path / name.strip('/') for name in
             ('/recipe', '/output', '/source/linux.tar.xz', '/driver', '/gem5/include/gem5/chimaera_m5.h')}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for name in ('/recipe', '/output', '/driver'):
        paths[name].mkdir(exist_ok=True)
    paths['/source/linux.tar.xz'].write_bytes(b'pinned Linux archive')
    atomic_json(paths['/recipe'] / 'policy.json', dict(version='5.15.180', sha256=digest(paths['/source/linux.tar.xz'])))
    required = ('MODULES', 'MODVERSIONS', 'ATA_PIIX', 'EXT4_FS', 'DEVTMPFS', 'DEVMEM', 'SERIAL_8250_CONSOLE')
    (paths['/recipe'] / 'config.fragment').write_text(''.join(f'CONFIG_{option}=y\n' for option in required))
    name = 'chimaera_m5' if backend == 'm5ops' else 'chimaera_dma'
    (paths['/driver'] / 'Makefile').write_text((ROOT / 'gem5-transport/kernel/Makefile').read_text())
    (paths['/driver'] / (name + '.c')).write_text('driver source')
    paths['/gem5/include/gem5/chimaera_m5.h'].write_text('#define CHM5_ABI_VERSION 1\n')
    commands = []

    def execute(argv, **kwargs):
        commands.append(argv)
        if argv[0] == 'tar':
            work = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--directory=')))
            (work / 'linux-5.15.180').mkdir()
        elif argv[-1] == 'x86_64_defconfig':
            (Path(argv[2]) / '.config').write_text('')
        elif 'vmlinux' in argv:
            for output in ('vmlinux', 'Module.symvers'):
                (Path(argv[2]) / output).write_text('pinned ' + output)
        elif any(arg.startswith('M=') for arg in argv):
            driver = Path(next(arg[2:] for arg in argv if arg.startswith('M=')))
            assert (driver / (name + '.c')).is_file()
            assert 'CHIMAERA_MODULES=' + name + '.o' in argv
            assert not any(arg.startswith('obj-m=') for arg in argv)
            assert any('vmlinux' in command and 'modules' in command for command in commands[:-1])
            (driver / (name + '.ko')).write_text('matching compiled module')

    def output(argv, **kwargs):
        if 'kernelrelease' in argv:
            return '5.15.180-chimaera\n'
        if argv[0] == 'modinfo':
            return ('5.15.180-chimaera' if matching_release else 'unrelated-release') + ' SMP modversions\n'
        return 'gcc pinned-toolchain\n'

    with patch.object(builder, 'Path', side_effect=lambda path: paths.get(str(path), Path(path))), \
         patch.object(builder.subprocess, 'run', side_effect=execute), \
         patch.object(builder.subprocess, 'check_output', side_effect=output):
        if not matching_release:
            with pytest.raises(ValueError, match='does not match the guest kernel release'):
                builder.build(2, backend)
            assert not (paths['/output'] / 'build.json').exists()
            return
        builder.build(2, backend)
    metadata = json.loads((paths['/output'] / 'build.json').read_text())
    assert metadata['backend'] == backend and metadata['module'] == name + '.ko'
    assert metadata['abi_version'] == 1
    assert metadata['kernel_release'] == metadata['vermagic'].split()[0]
    assert {path.name for path in paths['/output'].iterdir()} == {
        'kernel', name + '.ko', 'Module.symvers', 'kernel.config', 'build.json'}


@pytest.mark.parametrize('selected', [None, 'chimaera_dma.o', 'chimaera_m5.o'])
def test_driver_selection_allows_kbuild_to_rewrite_module_paths(tmp_path, selected):
    # Exercise GNU make's command-line precedence, which a mocked build misses.
    # Kbuild must be able to prefix obj-m with the external module directory.
    makefile = tmp_path / 'Kbuild-test'
    makefile.write_text(
        'include $(src)/Makefile\n'
        'obj-m := $(addprefix $(src)/,$(obj-m))\n'
        'all:\n\t@echo $(obj-m)\n')
    source = ROOT / 'gem5-transport/kernel'
    command = ['make', '--no-print-directory', '-f', str(makefile), 'src=' + str(source)]
    if selected:
        command.append('CHIMAERA_MODULES=' + selected)
    objects = subprocess.check_output(command, text=True).split()
    expected = [selected] if selected else ['chimaera_dma.o', 'chimaera_m5.o']
    assert objects == [str(source / name) for name in expected]


@pytest.mark.parametrize('backend', ['dma', 'm5ops'])
@pytest.mark.parametrize('valid_abi', [False, True])
def test_guest_driver_probe_checks_actual_abi_without_booting(backend, valid_abi, capsys):
    source = recipe('smoke').driver_probe(backend, '5.15.180-chimaera')
    guest_python = source.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY\n', 1)[0]
    lanes = []
    opened = []
    def open_device(path, flags):
        opened.append(path)
        return len(opened)
    def ioctl(fd, command, data, mutate):
        if backend == 'dma':
            assert command == 0x80184310
            data[:] = struct.pack('<6I', 1 if valid_abi else 2, 1, 2, 1048576, 4096, 0)
        else:
            assert command == 0xc0204d20
            abi, lane, *_ = struct.unpack('<6IQ', data)
            if abi == 2:
                raise OSError(errno.EINVAL, 'unsupported ABI')
            lanes.append(lane)
            data[:] = struct.pack('<6IQ', 1 if valid_abi else 2, lane, 0, 1234, 1, 4096, 1)
    libc = SimpleNamespace(syscall=lambda number, fd, parameters, flags: 0)
    fake_os = SimpleNamespace(open=open_device, close=lambda fd: None, O_RDONLY=0, O_RDWR=2,
        O_NONBLOCK=2048, uname=lambda: SimpleNamespace(release='5.15.180-chimaera'), getpid=lambda: 1234)
    fake_mmap = SimpleNamespace(MAP_SHARED=1, PROT_READ=1, PROT_WRITE=2)
    class Pages(bytearray):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    fake_mmap.mmap = lambda fd, size, **kwargs: Pages(size)
    with patch.object(ctypes, 'CDLL', return_value=libc), patch.object(fcntl, 'ioctl', side_effect=ioctl), \
         patch.dict('sys.modules', os=fake_os, mmap=fake_mmap):
        if valid_abi:
            exec(compile(guest_python, 'guest-driver-probe', 'exec'), {})
        else:
            with pytest.raises(AssertionError):
                exec(compile(guest_python, 'guest-driver-probe', 'exec'), {})
            assert 'CHIMAERA_DRIVER_PROBE' not in capsys.readouterr().out
            return
    record = json.loads(capsys.readouterr().out.split('CHIMAERA_DRIVER_PROBE ', 1)[1])
    assert record['backend'] == backend and record['abi_version'] == 1
    assert record['device'] == ('/dev/chimaera-m5' if backend == 'm5ops' else '/dev/chimaera-dma')
    assert record['device'] in opened
    if backend == 'm5ops':
        assert lanes == record['lanes'] == [0, 1]


@pytest.mark.parametrize('namespace', ['__main__', '__m5_main__'])
@pytest.mark.parametrize('backend', ['dma', 'm5ops'])
def test_smoke_config_runs_and_writes_probe_result_in_gem5_namespace(tmp_path, namespace, backend):
    board, processor, simulator = Mock(), Mock(), Mock()
    processor.get_cores.return_value = [Mock(), Mock()]
    simulator.get_last_exit_event_cause.return_value = 'm5_exit instruction encountered'
    simulator.get_last_exit_event_code.return_value = 0
    modules = {}

    def module(name, **attributes):
        parts = name.split('.')
        for index in range(1, len(parts) + 1):
            parent = '.'.join(parts[:index])
            if parent not in modules:
                modules[parent] = ModuleType(parent)
                modules[parent].__path__ = []
        for key, value in attributes.items():
            setattr(modules[name], key, value)

    module('m5', options=SimpleNamespace(outdir=str(tmp_path)))
    module('gem5.components.boards.x86_board', X86Board=Mock(return_value=board))
    module('gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy', MESITwoLevelCacheHierarchy=Mock())
    module('gem5.components.memory', SingleChannelDDR3_1600=Mock())
    module('gem5.components.processors.cpu_types', CPUTypes=SimpleNamespace(KVM='kvm'))
    module('gem5.components.processors.simple_processor', SimpleProcessor=Mock(return_value=processor))
    module('gem5.isas', ISA=SimpleNamespace(X86='x86'))
    module('gem5.resources.resource', DiskImageResource=Mock(), KernelResource=Mock())
    module('gem5.simulate.simulator', Simulator=Mock(return_value=simulator))
    module('chimaera_gem5', configure_ticks=Mock(), guest_start_script=Mock(return_value='boot script'))
    module('chimaera_dma', DmaCacheHierarchy=Mock(), attach_dma=Mock())
    module('chimaera_m5', attach_m5=Mock())
    probe = dict(backend=backend, abi_version=1, kernel_release='5.15.180-chimaera')
    if backend == 'm5ops':
        probe.update(device='/dev/chimaera-m5', lanes=[0, 1])
    (tmp_path / 'board.pc.com_1.device').write_text('CHIMAERA_DRIVER_PROBE ' + json.dumps(probe) + '\n')
    config = ROOT / 'orchestrator/kernel/smoke.py'
    with patch.dict('sys.modules', modules), patch('sys.path', []), \
         patch('sys.argv', [str(config), '--image', 'disk', '--kernel', 'kernel',
                            '--module', 'driver', '--backend', backend]):
        runpy.run_path(str(config), run_name=namespace)
    simulator.run.assert_called_once_with()
    attach = modules['chimaera_m5'].attach_m5 if backend == 'm5ops' else modules['chimaera_dma'].attach_dma
    attach.assert_called_once_with(board, str(tmp_path / 'h2g'), str(tmp_path / 'g2h'))
    record = json.loads((tmp_path / 'result.json').read_text())
    assert record == dict(probe, status='passed', cause='m5_exit instruction encountered', code=0)
    assert (tmp_path / 'check-driver.sh').is_file()
