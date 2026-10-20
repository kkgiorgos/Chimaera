"""KVM boot gate for the actual guest disk, kernel and DMA module."""
import argparse
import json
from pathlib import Path
import sys

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.memory import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.simulator import Simulator

sys.path.insert(0, '/opt/chimaera/ros/share/chimaera_ros_bridge/config')
from chimaera_dma import DmaCacheHierarchy, attach_dma
from chimaera_gem5 import configure_ticks, guest_start_script

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--image', required=True)
parser.add_argument('--kernel', required=True)
parser.add_argument('--module', required=True)
args = parser.parse_args()
configure_ticks()
processor = SimpleProcessor(cpu_type=CPUTypes.KVM, isa=ISA.X86, num_cores=2)
for core in processor.get_cores():
    core.get_simobject().usePerf = False
board = X86Board(clk_freq='3GHz', processor=processor,
    memory=SingleChannelDDR3_1600(size='3GiB'),
    cache_hierarchy=DmaCacheHierarchy(l1d_size='16KiB', l1d_assoc=8,
                                    l1i_size='16KiB', l1i_assoc=8,
                                    l2_size='256KiB', l2_assoc=16, num_l2_banks=1))
output = Path(m5.options.outdir)
script = output / 'check-driver.sh'
script.write_text('''#!/bin/bash
set -euo pipefail
python3 - <<'PY'
import ctypes, fcntl, os, struct
libc = ctypes.CDLL(None, use_errno=True)
fd = os.open('/tmp/chimaera-driver.ko', os.O_RDONLY)
try:
    if libc.syscall(313, fd, b'buffer_mb=1 receive_slots=1 transmit_slots=2', 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
finally:
    os.close(fd)
fd = os.open('/dev/chimaera-dma', os.O_RDWR | os.O_NONBLOCK)
try:
    data = bytearray(24)
    fcntl.ioctl(fd, 0x80184310, data, True)
    abi, rx, tx, size, page, reserved = struct.unpack('<6I', data)
    assert (abi, rx, tx, size, page) == (1, 1, 2, 1048576, 4096), (abi, rx, tx, size, page)
finally:
    os.close(fd)
print('CHIMAERA_KERNEL_DMA_OK', os.uname().release, flush=True)
PY
''')
script.chmod(0o755)
boot = guest_start_script('/tmp/check-driver.sh', [
    '/tmp/check-driver.sh=' + str(script), '/tmp/chimaera-driver.ko=' + args.module])
board.set_kernel_disk_workload(kernel=KernelResource(local_path=args.kernel),
    disk_image=DiskImageResource(local_path=args.image),
    kernel_args=['earlyprintk=ttyS0', 'console=ttyS0', 'lpj=7999923',
                 'root=/dev/sda2', 'mce=off', 'acpi=off'], readfile_contents=boot)
attach_dma(board, str(output / 'h2g'), str(output / 'g2h'))
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
(output / 'result.json').write_text(json.dumps(dict(status='passed', cause=cause, code=code)) + '\n')
