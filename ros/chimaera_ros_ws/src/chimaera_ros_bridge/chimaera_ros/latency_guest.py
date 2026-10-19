"""Guest session supervisor for the single-boot latency benchmark."""
import ctypes
import subprocess
import time


def read_script(read):
    chunks = []
    offset = 0
    while offset < 1024 * 1024:
        buffer = ctypes.create_string_buffer(4096)
        count = read(buffer, len(buffer), offset)
        if count > len(buffer):
            raise RuntimeError('Invalid m5 readfile length')
        chunks.append(buffer.raw[:count])
        offset += count
        if count < len(buffer):
            script = b''.join(chunks)
            if script == b'CHIMAERA_END_CASE\n':
                return None  # KVM may run past its workend exit before pausing.
            if not script.startswith(b'#!/bin/bash\n'):
                raise RuntimeError('Missing next-case readfile script')
            return script
    raise RuntimeError('Next-case readfile exceeds 1 MiB')


def supervise(root, cpu_type):
    library = ctypes.CDLL(root + '/app/lib/libchimaera_gem5_clock'
                          + ('_addr' if cpu_type == 'kvm' else '') + '.so')
    read = library.chimaera_gem5_read_file
    read.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64]
    read.restype = ctypes.c_uint64
    end = library.chimaera_gem5_case_end
    end.argtypes = []
    end.restype = None
    script = ('#!/bin/bash\nexec ' + root + '/guest_start\n').encode()
    while True:
        subprocess.run(['bash'], input=script, check=True)
        # runner has reaped both the bridge and endpoint before this barrier.
        end()
        script = read_script(read)
        while script is None:
            time.sleep(.001)
            script = read_script(read)


if __name__ == '__main__':
    import sys
    supervise(sys.argv[1], sys.argv[2])
