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
    # A clean m5 exit can arrive before Linux drains its serial output queue.
    # Export the proof synchronously instead of using UART text as the gate.
    return script + """record = dict(probe, kernel_release=os.uname().release)
with open('/tmp/chimaera-driver-probe.json', 'w') as output:
    json.dump(record, output)
print('CHIMAERA_DRIVER_PROBE ' + json.dumps(record), flush=True)
PY
/usr/local/bin/m5 --addr writefile /tmp/chimaera-driver-probe.json driver-probe.json >/dev/null
"""


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
    parser.add_argument('--export-stress', action='store_true',
                        help='Exercise concurrent address-mode file exports on both KVM cores')
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
    proof = output / 'driver-probe.json'
    proof.unlink(missing_ok=True)
    script = output / 'check-driver.sh'
    content = driver_probe(args.backend, args.expected_release)
    files = ['/tmp/check-driver.sh=' + str(script), '/tmp/chimaera-driver.ko=' + args.module]
    if args.export_stress:
        content += '''python3 - <<'PY'
import ctypes, multiprocessing, os
barrier = multiprocessing.Barrier(2)
def export(cpu):
    os.sched_setaffinity(0, {cpu})
    library = ctypes.CDLL('/tmp/export-clock.so')
    write = library.chimaera_gem5_write_file
    write.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p]
    write.restype = ctypes.c_uint64
    name = ('export-stress-%d.txt' % cpu).encode()
    barrier.wait(timeout=30)
    for index in range(512):
        data = ('%d:%d\\n' % (cpu, index)).encode()
        assert write(data, len(data), 0 if index == 0 else offset, name) == len(data)
        offset = (0 if index == 0 else offset) + len(data)
children = [multiprocessing.Process(target=export, args=(cpu,)) for cpu in (0, 1)]
for child in children: child.start()
for child in children:
    child.join(60)
    assert child.exitcode == 0, child.exitcode
PY
'''
        files.append('/tmp/export-clock.so=/opt/chimaera/ros/lib/libchimaera_gem5_clock_addr.so')
        for cpu in (0, 1):
            (output / f'export-stress-{cpu}.txt').unlink(missing_ok=True)
    script.write_text(content)
    script.chmod(0o755)
    boot = guest_start_script('/tmp/check-driver.sh', files)
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
    try:
        probe = json.loads(proof.read_text())
    except (FileNotFoundError, ValueError) as error:
        raise RuntimeError('Guest kernel/driver ABI probe did not export valid proof; inspect guest serial') from error
    if not isinstance(probe, dict) or probe.get('backend') != args.backend:
        raise RuntimeError('Guest kernel/driver ABI probe exported invalid backend proof; inspect guest serial')
    if args.export_stress:
        for cpu in (0, 1):
            expected = ''.join(f'{cpu}:{index}\n' for index in range(512))
            if (output / f'export-stress-{cpu}.txt').read_text() != expected:
                raise RuntimeError('Concurrent KVM file exports lost or corrupted records')
        probe['concurrent_exports'] = 1024
    (output / 'result.json').write_text(json.dumps(dict(probe, status='passed', cause=cause, code=code)) + '\n')


if __name__ in ('__main__', '__m5_main__'):
    main()
