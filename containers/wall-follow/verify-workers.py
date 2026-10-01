#!/usr/bin/env python3
"""Exercise real worker rendering, concurrent suites, output locks, and cleanup."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time


def inspect(name):
    result = subprocess.run(["docker", "container", "inspect", name], capture_output=True, text=True)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def wait_for(predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError("Timed out waiting for worker; inspect the saved logs")


def assert_policy(info, output):
    config = info["HostConfig"]
    assert info["State"]["Running"]
    assert config["NetworkMode"] == "none" and config["IpcMode"] == "private"
    assert config["ReadonlyRootfs"] and not config["Privileged"]
    assert config["CapDrop"] == ["ALL"] and "no-new-privileges" in config["SecurityOpt"]
    assert config["NanoCpus"] == 2_000_000_000 and config["Memory"] == 8 * 1024**3
    assert config["MemorySwap"] == config["Memory"] and config["PidsLimit"] == 512
    assert config["ShmSize"] == 256 * 1024**2 and "/tmp" in config["Tmpfs"]
    assert info["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
    assert os.getuid() != 0
    writable = [item for item in info["Mounts"] if item["Type"] == "bind" and item["RW"]]
    assert len(writable) == 1 and Path(writable[0]["Source"]) == output and writable[0]["Destination"] == "/output"
    assert all(not item["RW"] for item in info["Mounts"] if item["Destination"].startswith("/assets/"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New verification directory")
    parser.add_argument("--image", default="chimaera-worker:jammy-humble-fortress")
    rendering = parser.add_mutually_exclusive_group()
    rendering.add_argument("--render-device", type=Path, default=Path("/dev/dri/renderD128"))
    rendering.add_argument("--software-rendering", action="store_true")
    args = parser.parse_args()
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    directory = Path(__file__).resolve().parent
    base = [sys.executable, str(directory / "worker.py")]
    common = ["--image", args.image]
    common += ["--software-rendering"] if args.software_rendering else ["--render-device", str(args.render_device)]
    jobs = []
    logs = []
    image_info = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image]))[0]
    args.image = image_info["Id"]
    common[1] = args.image
    evidence = {"image_id": args.image, "image_size_bytes": image_info["Size"]}

    def launch(action, name, extra=(), environment=None):
        output = root / name
        log = (root / f"{name}.launcher.log").open("w")
        logs.append(log)
        command = [*base, action, "--output", str(output), *common, *extra]
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment)
        jobs.append((process, output))
        return process, output

    def running(output):
        record = output / "worker.json"
        if not record.is_file():
            return None
        value = inspect(json.loads(record.read_text())["container_name"])
        return value if value and value["State"]["Running"] else None

    def finish(process, output, expected=0):
        status = process.wait(timeout=150)
        if status != expected:
            raise RuntimeError(f"Worker exit {status}, expected {expected}: {output}")
        record = json.loads((output / "worker.json").read_text())
        assert record["exit_code"] == expected
        assert inspect(record["container_name"]) is None
        assert all(path.stat().st_uid == os.getuid() for path in output.rglob("*") if not path.is_symlink())
        return record

    try:
        checks = [launch("check", f"check-{index}") for index in (1, 2)]
        snapshots = wait_for(lambda: [running(output) for _, output in checks]
                            if all(running(output) for _, output in checks) else None)
        for (_, output), info in zip(checks, snapshots):
            assert_policy(info, output)
        (root / "concurrent-check-containers.json").write_text(json.dumps(snapshots, indent=2) + "\n")
        for process, output in checks:
            finish(process, output)
        probes = [json.loads((output / "check/isolation.json").read_text()) for _, output in checks]
        for (_, output), probe in zip(checks, probes):
            assert probe["token"] == json.loads((output / "worker.json").read_text())["token"]
            assert all(value == probe["token"] for value in probe["responses"].values())
            verification = json.loads((output / "check/verification.json").read_text())
            assert verification["kvm_create_vm"] and verification["kvm_api"] == 12 and verification["sensors"]
        assert probes[0]["token"] != probes[1]["token"]
        assert all(probes[0]["namespaces"][name] != probes[1]["namespaces"][name] for name in ("net", "ipc", "mnt"))
        evidence["concurrent_checks"] = probes
        print("Concurrent socket, KVM, headless lidar and namespace checks passed", flush=True)

        smoke = directory / "experiments/smoke.json"
        extra = ["--local", "--config", str(smoke), "--architecture", "runtime-check", "--warmup", "0"]
        suites = [launch("run", f"local-{index}", extra) for index in (1, 2)]
        snapshots = wait_for(lambda: [running(output) for _, output in suites]
                            if all(running(output) for _, output in suites) else None)
        for (_, output), info in zip(suites, snapshots):
            assert_policy(info, output)
        (root / "concurrent-suite-containers.json").write_text(json.dumps(snapshots, indent=2) + "\n")
        suite_results = []
        for process, output in suites:
            finish(process, output)
            attempt, = (output / "suite/runs").glob("*/attempt_*")
            metadata = json.loads((attempt / "metadata.json").read_text())
            assert metadata["completed"] and metadata["implementation"] == "cpp"
            assert metadata["provenance"]
            with (attempt / "samples.csv").open() as stream:
                samples = list(csv.DictReader(stream))
            with (attempt / "poses.csv").open() as stream:
                poses = list(csv.DictReader(stream))
            movement = max(math.hypot(float(row["x"]) - float(poses[0]["x"]),
                                      float(row["y"]) - float(poses[0]["y"])) for row in poses)
            assert len(samples) >= 3 and len(poses) >= 3 and movement > 0.02
            assert any(float(row["linear_cmd"]) > 0.05 for row in samples)
            suite_results.append({"output": str(output), "samples": len(samples), "poses": len(poses), "movement_m": movement})
        evidence["concurrent_local_suites"] = suite_results
        process, output = launch("run", "local-1", [*extra, "--resume"])
        finish(process, output)
        assert len(list((output / "suite/runs").glob("*/attempt_*"))) == 1
        evidence["resume_skips_completed"] = True
        variations = [["--cpus", "1"], ["--memory", "9g"]]
        if not args.software_rendering:
            variations.append(["--software-rendering"])
        for variation in variations:
            resource_common = ["--image", args.image] if variation[0] == "--software-rendering" else common
            mismatch = subprocess.run([*base, "run", "--output", str(output), *resource_common,
                                       *extra, "--resume", *variation], capture_output=True, text=True)
            (root / f"resume-mismatch-{variation[0][2:]}.log").write_text(mismatch.stdout + mismatch.stderr)
            assert mismatch.returncode == 2 and "Cannot resume" in mismatch.stderr
        evidence["changed_resource_resume_refused"] = True
        print("Two concurrent C++ controller suites and resume check passed", flush=True)

        long_config = root / "interrupt.json"
        config = json.loads(smoke.read_text())
        config["fixed"]["duration"] = 60.0
        long_config.write_text(json.dumps(config) + "\n")
        extra = ["--local", "--config", str(long_config), "--architecture", "interrupt-check", "--warmup", "0"]
        process, output = launch("run", "interrupt", extra)
        info = wait_for(lambda: running(output))
        assert_policy(info, output)
        probe = "import errno; from pathlib import Path\ntry:\n Path('/assets/experiment.json').open('a')\nexcept OSError as error:\n assert error.errno == errno.EROFS, error\n print('input mount is read-only')\nelse:\n raise RuntimeError('writable input mount')"
        subprocess.run(["docker", "exec", info["Id"], "python3", "-c", probe], check=True)
        wait_for(lambda: any(len(path.read_text().splitlines()) >= 4 for path in (output / "suite").glob("runs/*/attempt_*/samples.csv")))
        duplicate = subprocess.run([*base, "run", "--output", str(output), *common, *extra, "--resume"], capture_output=True, text=True)
        (root / "duplicate-writer.log").write_text(duplicate.stdout + duplicate.stderr)
        assert duplicate.returncode == 2 and "Another worker owns" in duplicate.stderr
        process.send_signal(signal.SIGINT)
        record = finish(process, output, expected=130)
        assert record["status"] == "interrupted"
        evidence.update(input_write_refused=True, duplicate_writer_refused=True, interrupted_worker_removed=True)
        print("Read-only input, duplicate writer refusal, and SIGINT cleanup passed", flush=True)

        # Delay only the client creation request to reproduce cancellation before a container exists.
        delayed = root / "delayed-cli"
        delayed.mkdir()
        marker = delayed / "creation-pending.json"
        executable = delayed / "docker"
        executable.write_text(f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\n"
                              f"if sys.argv[1] in ('create', 'run'):\n"
                              f" Path({str(marker)!r}).write_text(json.dumps({{'pid': os.getpid()}}))\n"
                              f" time.sleep(60)\n raise SystemExit(99)\n"
                              f"os.execv({shutil.which('docker')!r}, ['docker', *sys.argv[1:]])\n")
        executable.chmod(0o755)
        environment = dict(os.environ, PATH=str(delayed) + os.pathsep + os.environ["PATH"])
        process, output = launch("run", "cancel-before-create", extra, environment)
        wait_for(marker.exists, timeout=20)
        started = time.monotonic()
        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
        finish(process, output, expected=130)
        elapsed = time.monotonic() - started
        assert elapsed < 10
        try:
            os.kill(json.loads(marker.read_text())["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError("Canceled Docker client remains alive")
        evidence["startup_cancel_with_delayed_client_seconds"] = elapsed
        print("Cancellation before container creation passed", flush=True)

        failing = root / "failing-cli"
        failing.mkdir()
        created = failing / "created"
        injected = failing / "injected"
        executable = failing / "docker"
        executable.write_text(f"#!{sys.executable}\nimport os, subprocess, sys\nfrom pathlib import Path\n"
                              f"real={shutil.which('docker')!r}\n"
                              f"if sys.argv[1] == 'create':\n"
                              f" result=subprocess.run([real, *sys.argv[1:]])\n"
                              f" if result.returncode == 0: Path({str(created)!r}).touch()\n"
                              f" raise SystemExit(result.returncode)\n"
                              f"if sys.argv[1:3] == ['container', 'inspect'] and Path({str(created)!r}).exists() and not Path({str(injected)!r}).exists():\n"
                              f" Path({str(injected)!r}).touch()\n"
                              f" sys.stderr.write('injected worker inspect failure')\n raise SystemExit(1)\n"
                              f"os.execv(real, ['docker', *sys.argv[1:]])\n")
        executable.chmod(0o755)
        environment = dict(os.environ, PATH=str(failing) + os.pathsep + os.environ["PATH"])
        process, output = launch("run", "inspect-error", extra, environment)
        record = finish(process, output, expected=2)
        assert record["status"] == "failed" and "injected worker inspect failure" in record["error"]
        assert record["finished_at"] and "start_command" not in record
        evidence["inspect_failure_recorded_and_container_removed"] = True
        print("Injected inspect failure recorded and created container removed", flush=True)
        missing = subprocess.run([*base, "run", "--output", str(root / "missing-guest"), *common,
                                  "--config", str(smoke), "--architecture", "baseline"], capture_output=True, text=True)
        (root / "missing-guest.log").write_text(missing.stdout + missing.stderr)
        assert missing.returncode == 2 and "require --guest-image and --kernel" in missing.stderr
        assert not (root / "missing-guest").exists()
        evidence["missing_guest_refused"] = True
        evidence["full_system_guest_boot"] = "pending prepared guest disk"
        (root / "verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    finally:
        for process, output in jobs:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    record = output / "worker.json"
                    if record.exists():
                        metadata = json.loads(record.read_text())
                        info = inspect(metadata["container_name"])
                        if info and info["Config"]["Labels"].get("io.chimaera.worker-token") == metadata["token"]:
                            subprocess.run(["docker", "rm", "--force", info["Id"]], check=True)
                    process.kill()
                    process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
