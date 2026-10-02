"""Verify the worker payload, dynamic libraries, and optional devices/sensors."""

import argparse
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import socket


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensors", action="store_true")
    parser.add_argument("--kvm", action="store_true")
    parser.add_argument("--isolation", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("/tmp/worker-check"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sockets = []
    if args.isolation:
        token = os.environ["CHIMAERA_WORKER_TOKEN"]
        marker = Path("/tmp/chimaera-worker-marker")
        if marker.exists():
            raise RuntimeError("Worker inherited another worker's private marker")
        marker.write_text(token)
        responses = {}
        for path in ("/tmp/chimaera_g2h.sock", "/tmp/chimaera_h2g.sock", "/tmp/chimaera_time.sock"):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            server.bind(path)
            server.listen(1)
            sockets.append(server)
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                client.connect(path)
                connection, _ = server.accept()
                with connection:
                    connection.sendall(token.encode())
                    responses[path] = client.recv(256).decode()
            if responses[path] != token:
                raise RuntimeError(f"Socket challenge failed: {path}")
        isolation = {"token": token, "responses": responses, "uid": os.getuid(),
                     "namespaces": {name: os.readlink(f"/proc/self/ns/{name}") for name in ("net", "ipc", "mnt")}}
        (args.output / "isolation.json").write_text(json.dumps(isolation, indent=2) + "\n")
        # Deliberately share the partition name for the concurrent sensor checks.
        os.environ["IGN_PARTITION"] = "chimaera-isolation-check"
    root = Path("/opt/chimaera")
    manifest_path = root / "worker-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for value, expected in manifest["files"].items():
        path = Path(value)
        if path.is_symlink() or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"Worker checksum mismatch: {path}")
        if stat.S_IMODE(path.stat().st_mode) != expected["mode"]:
            raise RuntimeError(f"Worker file mode mismatch: {path}")
    for value, target in manifest["symlinks"].items():
        path = Path(value)
        if not path.is_symlink() or str(path.readlink()) != target or not path.exists():
            raise RuntimeError(f"Worker symlink mismatch or missing target: {path}")
    packages = subprocess.check_output(["dpkg-query", "--show", "--showformat=${binary:Package}\t${Version}\n"], text=True)
    if sorted(packages.splitlines()) != sorted((root / "packages.tsv").read_text().splitlines()):
        raise RuntimeError("Installed package versions differ from the SDK inventory")
    for module in ("rclpy", "launch", "launch_ros", "ros2launch", "ros2pkg", "numpy", "yaml", "catkin_pkg", "em"):
        importlib.import_module(module)
    elves = 0
    for value in manifest["files"]:
        path = Path(value)
        with path.open("rb") as stream:
            is_elf = stream.read(4) == b"\x7fELF"
        if is_elf:
            result = subprocess.run(["ldd", str(path)], capture_output=True, text=True)
            output = result.stdout + result.stderr
            if "not found" in output or (result.returncode and "not a dynamic executable" not in output):
                raise RuntimeError(f"Unresolved ELF: {path}\n{result.stdout}{result.stderr}")
            elves += 1
    subprocess.run([str(root / "gem5/build/X86/gem5.opt"), "--outdir", str(args.output / "gem5"),
                    str(root / "scripts/verify-gem5.py")], check=True)
    result = {"manifest_sha256": sha256(manifest_path), "verified_files": len(manifest["files"]),
              "resolved_elf_files": elves, "uid": os.getuid()}
    if args.kvm:
        with open("/dev/kvm", "rb+", buffering=0) as device:
            version = fcntl.ioctl(device, 0xAE00)
            if version != 12:
                raise RuntimeError(f"Unexpected KVM API: {version}")
            vm = fcntl.ioctl(device, 0xAE01, 0)
            os.close(vm)
            result["kvm_api"] = version
            result["kvm_create_vm"] = True
    if args.sensors:
        subprocess.run(["python3", str(root / "scripts/verify-gazebo.py"),
                        "--output", str(args.output / "sensors")], check=True)
        result["sensors"] = True
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    for server in sockets:
        server.close()


if __name__ == "__main__":
    main()
