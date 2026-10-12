#!/usr/bin/env python3
"""Check m5 functional-write ordering on Timing/O3 and classic/Ruby caches."""
import argparse
import itertools
import json
import socket
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEM5 = HERE.parents[2]
SOCKET = Path("/tmp/chimaera_h2g.sock")
HEADER = (3).to_bytes(8, "big")


def run_probe(binary, output, cpu, cache, operation, timeout):
    directory = output / f"{cpu}-{cache}-{operation}"
    directory.mkdir()
    source = output / "controller-header.bin"
    source.write_bytes(HEADER)
    command = [
        str(binary),
        "--outdir=" + str(directory),
        str(HERE / "config.py"),
        "--binary",
        str(output / operation),
        "--cpu",
        cpu,
        "--cache",
        cache,
        "--readfile",
        str(source),
    ]
    server = worker = None
    owns_socket = False
    failures = []
    try:
        if operation == "receive":
            # Fixed Chimaera paths: refuse even stale entries rather than
            # connect to, unlink, or disturb an existing transport session.
            if SOCKET.exists():
                raise RuntimeError(
                    f"{SOCKET} already exists; "
                    "stop other Chimaera sessions first"
                )
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(SOCKET))
            owns_socket = True
            server.listen(1)
            server.settimeout(timeout)

            def send_header():
                try:
                    with server.accept()[0] as connection:
                        connection.sendall(HEADER)
                except Exception as error:
                    failures.append(str(error))

            worker = threading.Thread(target=send_header)
            worker.start()
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    finally:
        if worker is not None:
            worker.join(timeout=timeout)
        if server is not None:
            server.close()
        if owns_socket:
            SOCKET.unlink(missing_ok=True)
    log = completed.stdout + completed.stderr
    (directory / "probe.log").write_text(log)
    passed = completed.returncode == 0 and not failures
    print(f"{directory.name}: {'PASS' if passed else 'FAIL'}", flush=True)
    if not passed:
        print("\n".join(log.splitlines()[-25:]), flush=True)
    return dict(
        cpu=cpu,
        cache=cache,
        operation=operation,
        returncode=completed.returncode,
        peer_errors=failures,
        passed=passed,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gem5", type=Path, default=GEM5 / "build/X86/gem5.opt"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--timeout", type=float, default=30, help="Wall seconds per probe"
    )
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("--timeout must be positive and at most 60 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, operation in [
        ("receive", "M5OP_CHIMAERA_RECV"),
        ("readfile", "M5OP_READ_FILE"),
    ]:
        subprocess.run(
            [
                "gcc",
                "-nostdlib",
                "-static",
                "-no-pie",
                "-I" + str(GEM5 / "include"),
                "-DPROBE_OP=" + operation,
                str(HERE / "probe.S"),
                "-o",
                str(output / name),
            ],
            check=True,
        )
    results = [
        run_probe(
            args.gem5.resolve(), output, cpu, cache, operation, args.timeout
        )
        for cpu, cache, operation in itertools.product(
            ("timing", "o3"), ("classic", "ruby"), ("receive", "readfile")
        )
    ]
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
