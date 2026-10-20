#!/usr/bin/env python3
"""Run the native reference and a fresh guest DMA probe on the same host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--gem5', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--module', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--cpu-type', choices=('timing', 'kvm'), default='timing')
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--echo-helper', type=Path,
                        help='Echo via host mailboxes at boundaries instead of device loopback')
    parser.add_argument('--step-us', type=int, default=2000)
    parser.add_argument('--debug-flags', help='Optional gem5 diagnostic trace (changes wall cost)')
    parser.add_argument('--dma-window', type=int, default=64)
    parser.add_argument('--direct-dma', action='store_true')
    args = parser.parse_args()
    if not 0 < args.step_us <= 1000000:
        parser.error('step-us must be between 1 and 1000000')
    args.output.mkdir(parents=True, exist_ok=False)
    config_snapshot = args.output / 'gem5_dma_probe.py'
    shutil.copyfile(args.config, config_snapshot)
    shutil.copyfile(__file__, args.output / 'run_dma_probe.py')
    native = subprocess.run([str(args.probe), 'native'], capture_output=True, text=True, check=True)
    (args.output / 'native.json').write_text(native.stdout)
    provenance = dict(host=platform.node(), loadavg=os.getloadavg(), cpu=args.cpu_type,
                      gem5_sha256=hashlib.sha256(args.gem5.read_bytes()).hexdigest(),
                      probe_sha256=hashlib.sha256(args.probe.read_bytes()).hexdigest(),
                      module_sha256=hashlib.sha256(args.module.read_bytes()).hexdigest(),
                      fresh_boot=True, step_us=args.step_us, dma_window=args.dma_window,
                      direct_dma=args.direct_dma,
                      host_mailbox_echo=bool(args.echo_helper),
                      kind='DMA microbenchmark; not end-to-end ROS latency')
    (args.output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    socket_path = str(args.output / 'timing.sock')
    command = [str(args.gem5), f'--outdir={args.output / "gem5"}', str(config_snapshot),
               '--runtime', str(args.runtime), '--image', str(args.image),
               '--kernel', str(args.kernel), '--module', str(args.module),
               '--probe', str(args.probe), '--cpu-type', args.cpu_type,
               '--socket-path', socket_path, '--dma-window', str(args.dma_window)]
    if args.debug_flags:
        command[1:1] = [f'--debug-flags={args.debug_flags}', '--debug-file=dma-debug.log']
    if args.direct_dma:
        command.append('--direct-dma')
    echo = None
    if args.echo_helper:
        h2g, g2h = args.output / 'h2g.mailbox', args.output / 'g2h.mailbox'
        command += ['--mailbox-h2g', str(h2g), '--mailbox-g2h', str(g2h)]
        echo = subprocess.Popen([str(args.echo_helper), str(h2g), str(g2h)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        if echo.stdout.readline().strip() != 'READY':
            raise RuntimeError('host mailbox initialization failed')
    (args.output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    start = time.monotonic()
    with (args.output / 'gem5.log').open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            while not Path(socket_path).exists():
                if process.poll() is not None:
                    raise RuntimeError(f'gem5 exited during startup: {process.returncode}')
                if time.monotonic() - start > args.timeout:
                    raise TimeoutError('guest startup timed out')
                time.sleep(.2)
            print('DMA_PROBE_READY', flush=True)
            origin_path = args.output / 'gem5/dma_probe_origin.json'
            if origin_path.exists():
                origin = json.loads(origin_path.read_text())
                expected = 'BaseTimingSimpleCPU' if args.cpu_type == 'timing' else 'X86KvmCPU'
                if origin['active_cpus'] != [expected, expected]:
                    raise RuntimeError('active guest CPU provenance does not match requested model')
            def request(line):
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(max(1, args.timeout - (time.monotonic() - start)))
                    client.connect(socket_path)
                    client.sendall((line + '\n').encode())
                    data = b''
                    while not data.endswith(b'\n'):
                        chunk = client.recv(4096)
                        if not chunk:
                            raise RuntimeError('timing server closed connection')
                        data += chunk
                    return data.decode().strip()
            while time.monotonic() - start < args.timeout:
                if echo:
                    echo.stdin.write('PUMP\n')
                    echo.stdin.flush()
                    if not echo.stdout.readline().strip().isdecimal():
                        raise RuntimeError('host mailbox exchange failed')
                    if request('CHIMAERA_INGRESS') != 'OK INGRESS':
                        raise RuntimeError('device ingress failed')
                response = request(f'STEP_US {args.step_us}')
                if response.startswith('DONE '):
                    break
                if not response.startswith('OK '):
                    raise RuntimeError(response)
            else:
                raise TimeoutError('guest DMA probe timed out')
            request('QUIT')
            require_result = args.output / 'gem5/chimaera-dma-probe.json'
            if not require_result.is_file():
                error_file = args.output / 'gem5/chimaera-dma-error.txt'
                reason = error_file.read_text().strip() if error_file.is_file() else 'no result exported'
                raise RuntimeError(f'guest DMA probe failed: {reason}')
            result = json.loads(require_result.read_text())
            if result['integrity'] != 'pass' or result['reset_reopen'] != 'pass':
                raise RuntimeError('device probe validation failed')
            provenance.update(result='complete', wall_seconds=time.monotonic() - start)
            print(json.dumps(result), flush=True)
        except Exception as error:
            provenance.update(result='failed', error=str(error), wall_seconds=time.monotonic() - start)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if echo:
                echo.stdin.close()
                try:
                    echo.wait(5)
                except subprocess.TimeoutExpired:
                    echo.kill()
                    echo.wait()
            (args.output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')


if __name__ == '__main__':
    main()
