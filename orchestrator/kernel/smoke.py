"""KVM boot gate for the actual guest disk, kernel and transport module."""
import argparse
import json
from pathlib import Path
import sys


def driver_probe(backend, expected_release=None):
    """Guest script: load without force, then exercise the driver's real ABI."""
    if backend not in ('dma', 'm5ops'):
        raise ValueError('Unsupported driver probe backend: ' + backend)
    parameters = (b'control_slots=1 bulk_slots=1 control_slot_bytes=4096 bulk_slot_bytes=4096'
                  if backend == 'm5ops' else b'buffer_mb=1 receive_slots=1 transmit_slots=2')
    script = '''#!/bin/bash
set -euo pipefail
python3 - <<'PY'
import ctypes, errno, fcntl, json, mmap, os, struct
'''
    script += 'expected_release = ' + repr(expected_release) + '\n'
    script += '''if expected_release is not None:
    assert os.uname().release == expected_release, (os.uname().release, expected_release)
libc = ctypes.CDLL(None, use_errno=True)
fd = os.open('/tmp/chimaera-driver.ko', os.O_RDONLY)
try:
'''
    script += '    if libc.syscall(313, fd, ' + repr(parameters) + ''', 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
finally:
    os.close(fd)
'''
    if backend == 'm5ops':
        script += '''for lane in (0, 1):
    fd = os.open('/dev/chimaera-m5', os.O_RDWR | os.O_NONBLOCK)
    try:
        # Reject an unsupported ABI before binding each independent lane.
        invalid = bytearray(struct.pack('<6IQ', 2, lane, 0, 0, 0, 0, 0))
        try:
            fcntl.ioctl(fd, 0xc0204d20, invalid, True)
        except OSError as error:
            assert error.errno == errno.EINVAL, error
        else:
            raise AssertionError('m5 driver accepted an unsupported ABI')
        data = bytearray(struct.pack('<6IQ', 1, lane, 0, 0, 0, 0, 0))
        fcntl.ioctl(fd, 0xc0204d20, data, True)
        abi, actual_lane, context, process, slots, size, generation = struct.unpack('<6IQ', data)
        assert (abi, actual_lane, process, slots, size) == (1, lane, os.getpid(), 1, 4096), tuple(data)
        assert context < 64 and generation > 0, (context, generation)
        with mmap.mmap(fd, size, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE) as pages:
            pages[0] = lane + 1
            assert pages[0] == lane + 1
    finally:
        os.close(fd)
print('CHIMAERA_KERNEL_M5_OK', os.uname().release, flush=True)
probe = dict(backend='m5ops', abi_version=1, device='/dev/chimaera-m5', lanes=[0, 1])
'''
    else:
        script += '''fd = os.open('/dev/chimaera-dma', os.O_RDWR | os.O_NONBLOCK)
try:
    data = bytearray(24)
    fcntl.ioctl(fd, 0x80184310, data, True)
    abi, rx, tx, size, page, reserved = struct.unpack('<6I', data)
    assert (abi, rx, tx, size, page) == (1, 1, 2, 1048576, 4096), (abi, rx, tx, size, page)
finally:
    os.close(fd)
print('CHIMAERA_KERNEL_DMA_OK', os.uname().release, flush=True)
probe = dict(backend='dma', abi_version=1, device='/dev/chimaera-dma')
'''
    return script + "print('CHIMAERA_DRIVER_PROBE ' + json.dumps(dict(probe, kernel_release=os.uname().release)), flush=True)\nPY\n"


def main():
    import m5
    from gem5.components.boards.x86_board import X86Board
    from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
    from gem5.components.memory import SingleChannelDDR3_1600
    from gem5.components.processors.cpu_types import CPUTypes
    from gem5.components.processors.simple_processor import SimpleProcessor
    from gem5.isas import ISA
    from gem5.resources.resource import DiskImageResource, KernelResource
    from gem5.simulate.simulator import Simulator

    sys.path.insert(0, '/opt/chimaera/ros/share/chimaera_ros_bridge/config')
    from chimaera_gem5 import configure_ticks, guest_start_script

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--kernel', required=True)
    parser.add_argument('--module', required=True)
    parser.add_argument('--backend', choices=('dma', 'm5ops'), default='dma')
    parser.add_argument('--expected-release')
    args = parser.parse_args()
    configure_ticks()
    processor = SimpleProcessor(cpu_type=CPUTypes.KVM, isa=ISA.X86, num_cores=2)
    for core in processor.get_cores():
        core.get_simobject().usePerf = False
    hierarchy = MESITwoLevelCacheHierarchy
    if args.backend == 'dma':
        from chimaera_dma import DmaCacheHierarchy
        hierarchy = DmaCacheHierarchy
    board = X86Board(clk_freq='3GHz', processor=processor,
        memory=SingleChannelDDR3_1600(size='3GiB'),
        cache_hierarchy=hierarchy(l1d_size='16KiB', l1d_assoc=8,
                                 l1i_size='16KiB', l1i_assoc=8,
                                 l2_size='256KiB', l2_assoc=16, num_l2_banks=1))
    output = Path(m5.options.outdir)
    script = output / 'check-driver.sh'
    script.write_text(driver_probe(args.backend, args.expected_release))
    script.chmod(0o755)
    boot = guest_start_script('/tmp/check-driver.sh', [
        '/tmp/check-driver.sh=' + str(script), '/tmp/chimaera-driver.ko=' + args.module])
    board.set_kernel_disk_workload(kernel=KernelResource(local_path=args.kernel),
        disk_image=DiskImageResource(local_path=args.image),
        kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923',
                     'root=/dev/sda2', 'mce=off', 'acpi=off'], readfile_contents=boot)
    if args.backend == 'dma':
        from chimaera_dma import attach_dma
        attach_dma(board, str(output / 'h2g'), str(output / 'g2h'))
    else:
        from chimaera_m5 import attach_m5
        attach_m5(board, str(output / 'h2g'), str(output / 'g2h'))
    simulator = Simulator(board=board)
    simulator.set_max_ticks(300 * 1_000_000_000_000)
    simulator.run()
    cause = simulator.get_last_exit_event_cause()
    code = simulator.get_last_exit_event_code()
    if cause != 'm5_exit instruction encountered' or code != 0:
        raise RuntimeError(f'Guest kernel/driver smoke failed: {cause} (code {code}); inspect guest serial')
    serial = (output / 'board.pc.com_1.device').read_text(errors='replace')
    if 'I/O error, dev sda' in serial:
        raise RuntimeError('Guest disk I/O errors during kernel/driver smoke; inspect guest serial')
    probes = [json.loads(line.split(' ', 1)[1]) for line in serial.splitlines()
              if line.startswith('CHIMAERA_DRIVER_PROBE ')]
    if len(probes) != 1 or probes[0].get('backend') != args.backend:
        raise RuntimeError('Guest kernel/driver ABI probe did not complete; inspect guest serial')
    (output / 'result.json').write_text(json.dumps(dict(probes[0], status='passed', cause=cause, code=code)) + '\n')


if __name__ in ('__main__', '__m5_main__'):
    main()
