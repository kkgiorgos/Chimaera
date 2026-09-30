#!/usr/bin/env python3
"""Run an isolated wall follow worker using the local Docker daemon."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import time
import uuid

from guest_assets import sha256, verify as verify_guest


def docker(*arguments, check=True, timeout=15):
    return subprocess.run(["docker", *arguments], check=check, capture_output=True, text=True, timeout=timeout)


def input_file(value):
    path = value.expanduser().resolve(strict=True)
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError(f"Input must be a readable file: {path}")
    if "," in str(path):
        raise ValueError(f"Docker mount paths cannot contain commas: {path}")
    return path


def fingerprint(path):
    info = path.stat()
    return {"path": str(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "device": info.st_dev, "inode": info.st_ino}


def mount(source, destination, readonly=True):
    return ["--mount", f"type=bind,source={source},target={destination}" + (",readonly" if readonly else "")]


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    for name in ("check", "run"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True, help="Worker directory; suites are stored under suite/")
        command.add_argument("--image", default="chimaera-worker:jammy-humble-fortress", help="Local Docker image tag or ID")
        command.add_argument("--cpus", type=float, default=2)
        command.add_argument("--memory", default="8g")
        rendering = command.add_mutually_exclusive_group()
        rendering.add_argument("--render-device", type=Path, default=Path("/dev/dri/renderD128"))
        rendering.add_argument("--software-rendering", action="store_true")
    commands.choices["check"].add_argument("--skip-kvm", action="store_true")
    run = commands.choices["run"]
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--architecture", required=True)
    run.add_argument("--local", action="store_true", help="Verify with the native controller; no guest assets required")
    run.add_argument("--guest-image", type=Path, help="Prepared guest disk, mounted read-only")
    run.add_argument("--kernel", type=Path)
    run.add_argument("--guest-assets", type=Path, help="Completed guest directory; verify disk/kernel hashes and use its root identity")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--keep-going", action="store_true")
    run.add_argument("--warmup", type=float, default=5)
    run.add_argument("--interval-us", type=int, default=50000)
    run.add_argument("--poll-us", type=int, default=10000)
    run.add_argument("--ratio", type=float, default=1)
    run.add_argument("--startup-timeout", type=int, default=300)
    return result


def run_worker(args):
    if os.getuid() == 0:
        raise ValueError("Run the launcher as a nonroot user with access to Docker and the required devices")
    if not math.isfinite(args.cpus) or args.cpus <= 0:
        raise ValueError("--cpus must be finite and positive")
    if not re.fullmatch(r"[1-9][0-9]*[bkmgBKMG]?", args.memory):
        raise ValueError("--memory must be a positive Docker size, such as 8g")
    output = args.output.expanduser().resolve()
    if "," in str(output):
        raise ValueError("Docker output paths cannot contain commas")
    image_info = json.loads(docker("image", "inspect", args.image).stdout)[0]
    image_id = image_info["Id"]
    if image_info["Architecture"] != "amd64" or image_info["Os"] != "linux":
        raise ValueError("Worker requires a linux/amd64 image")
    has_kvm = args.action == "check" and not args.skip_kvm or args.action == "run" and not args.local
    identity = {"image_id": image_id, "action": args.action, "cpus": args.cpus, "memory": args.memory.lower()}
    inputs = []
    command = ["python3", "/opt/chimaera/scripts/verify-worker.py", "--sensors", "--isolation", "--output", "/output/check"]
    if args.action == "run":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.architecture):
            raise ValueError("--architecture must contain letters, digits, underscores, dots or hyphens")
        if not math.isfinite(args.warmup) or args.warmup < 0:
            raise ValueError("--warmup must be finite and nonnegative")
        if not 0 < args.poll_us < args.interval_us <= 3600000000:
            raise ValueError("Require 0 < --poll-us < --interval-us <= 3600000000")
        if not math.isfinite(args.ratio) or args.ratio <= 0 or args.startup_timeout <= 0:
            raise ValueError("--ratio and --startup-timeout must be positive")
        config = input_file(args.config)
        settings = json.loads(config.read_text())
        if not isinstance(settings, dict):
            raise ValueError("Experiment configuration must be a JSON object")
        fixed, sweep = settings.get("fixed", {}), settings.get("sweep", {})
        if not isinstance(fixed, dict) or not isinstance(sweep, dict):
            raise ValueError("fixed and sweep must be JSON objects")
        gui_values = sweep.get("gui", [fixed.get("gui", False)])
        if not isinstance(gui_values, list) or any(value is not False for value in gui_values):
            raise ValueError("Worker configurations require gui=false")
        identity.update(architecture=args.architecture, local=args.local,
                        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(), warmup=args.warmup)
        inputs.append((config, "/assets/experiment.json"))
        command = ["python3", "/opt/chimaera/ws/scripts/run_experiments.py", "--config", "/assets/experiment.json",
                   "--output", "/output/suite", "--architecture", args.architecture,
                   "--no-plot", "--warmup", str(args.warmup)]
        if args.local:
            if args.guest_image or args.kernel or args.guest_assets:
                raise ValueError("--local does not accept guest assets")
        else:
            root_device = "/dev/sda2"
            if args.guest_assets:
                if args.guest_image or args.kernel:
                    raise ValueError("--guest-assets cannot be combined with --guest-image or --kernel")
                assets = args.guest_assets.expanduser().resolve(strict=True)
                manifest = verify_guest(assets)
                stack = image_info["Config"]["Labels"].get("io.chimaera.stack")
                if manifest["profile"] != stack:
                    raise ValueError("Guest and worker stack profiles differ")
                args.guest_image, args.kernel = assets / "disk.img", assets / "kernel"
                root_device = "PARTUUID=" + manifest["layout"]["root_partuuid"]
                identity["guest_manifest_sha256"] = sha256(assets / "manifest.json")
                inputs.append((input_file(assets / "manifest.json"), "/assets/guest-manifest.json"))
            if not args.guest_image or not args.kernel:
                raise ValueError("gem5 runs require --guest-assets or both --guest-image and --kernel")
            guest = input_file(args.guest_image)
            kernel = input_file(args.kernel)
            if guest == kernel:
                raise ValueError("Guest disk and kernel must be separate files")
            identity.update(guest=fingerprint(guest), kernel=fingerprint(kernel),
                            root_device=root_device,
                            interval_us=args.interval_us, poll_us=args.poll_us,
                            ratio=args.ratio, startup_timeout=args.startup_timeout)
            inputs += [(guest, "/assets/guest.img"), (kernel, "/assets/kernel")]
            command += ["--gem5", "--gem5-root", "/opt/chimaera/gem5", "--image", "/assets/guest.img",
                        "--kernel", "/assets/kernel", "--root-device", root_device,
                        "--interval-us", str(args.interval_us),
                        "--poll-us", str(args.poll_us), "--ratio", str(args.ratio),
                        "--startup-timeout", str(args.startup_timeout)]
        if args.resume:
            command.append("--resume")
        if args.keep_going:
            command.append("--keep-going")
    elif has_kvm:
        command.append("--kvm")
    for path, _ in inputs:
        if path == output or output in path.parents:
            raise ValueError(f"Inputs must be outside the writable output directory: {path}")
    devices = []
    if has_kvm:
        devices.append(Path("/dev/kvm"))
    if not args.software_rendering:
        devices.append(args.render_device.expanduser().resolve(strict=True))
        identity["rendering"] = {"device": str(devices[-1]), "device_number": devices[-1].stat().st_rdev}
    else:
        identity["rendering"] = {"software": True}
    for device in devices:
        if not stat.S_ISCHR(device.stat().st_mode) or not os.access(device, os.R_OK | os.W_OK):
            raise ValueError(f"Device must be accessible for reading and writing: {device}")
    name = "chimaera-worker-" + hashlib.sha256(os.fsencode(output)).hexdigest()[:20]
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Another worker owns {output}") from None
        previous = output / "worker.json"
        resume = args.action == "run" and args.resume
        if resume:
            if not previous.is_file() or json.loads(previous.read_text())["identity"] != identity:
                raise ValueError("Cannot resume: worker image, experiment, or guest assets differ")
        elif any(path.name != ".worker.lock" for path in output.iterdir()):
            raise ValueError(f"Output is not empty; choose a new directory: {output}")
        if docker("container", "inspect", name, check=False).returncode == 0:
            raise ValueError(f"Container {name} still owns this output; stop it before retrying")
        token = uuid.uuid4().hex
        attempt = output / "launches" / token
        attempt.mkdir(parents=True)
        argv = ["docker", "create", "--rm", "--init", "--name", name,
                "--label", f"io.chimaera.worker-token={token}", "--network", "none", "--ipc", "private",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", f"{os.getuid()}:{os.getgid()}", "--cpus", str(args.cpus),
                "--memory", args.memory, "--memory-swap", args.memory, "--pids-limit", "512",
                "--shm-size", "256m", "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=512m",
                "--env", f"IGN_PARTITION={name}", "--env", f"CHIMAERA_WORKER_TOKEN={token}",
                "--cidfile", str(attempt / "container.id")]
        for device in devices:
            argv += ["--device", str(device)]
        groups = {path.stat().st_gid for path in [*devices, *(path for path, _ in inputs), output]}
        for gid in sorted(groups):
            argv += ["--group-add", str(gid)]
        if args.software_rendering:
            argv += ["--env", "LIBGL_ALWAYS_SOFTWARE=1"]
        for path, destination in inputs:
            argv += mount(path, destination)
        argv += mount(output, "/output", readonly=False) + [image_id, *command]
        record = {"identity": identity, "container_name": name, "token": token, "command": argv,
                  "started_at": datetime.now(timezone.utc).isoformat(), "status": "running"}

        def save():
            text = json.dumps(record, indent=2) + "\n"
            for path in (attempt / "worker.json", previous):
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(text)
                temporary.replace(path)

        save()
        print(shlex.join(argv), flush=True)
        print(f"Worker log: {attempt / 'worker.log'}", flush=True)
        interrupted = None

        def interrupt(signum, _frame):
            nonlocal interrupted
            interrupted = signum

        handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}

        def owned_container():
            info = docker("container", "inspect", name, check=False, timeout=5)
            if info.returncode:
                if "No such" in info.stderr:
                    return None
                raise RuntimeError(f"Cannot inspect worker for cleanup: {info.stderr.strip()}")
            value = json.loads(info.stdout)[0]
            if value["Config"]["Labels"].get("io.chimaera.worker-token") == token:
                return value
            return None

        process = None
        try:
            with (attempt / "worker.log").open("w") as log:
                process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                while process.poll() is None:
                    if interrupted:
                        # A canceled create request can only leave a stopped container.
                        process.terminate()
                        break
                    time.sleep(0.25)
                try:
                    status = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    status = process.wait()
                if status == 0 and not interrupted:
                    container = owned_container()
                    if container is None:
                        raise RuntimeError("Created worker container disappeared before start")
                    start = ["docker", "start", "--attach", container["Id"]]
                    record["start_command"] = start
                    save()
                    process = subprocess.Popen(start, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    while process.poll() is None:
                        if interrupted:
                            docker("stop", "--signal", "SIGINT", "--timeout", "30", container["Id"],
                                   check=False, timeout=45)
                            break
                        time.sleep(0.25)
                    status = process.wait(timeout=40)
            if status < 0:
                status = 128 - status
            if interrupted:
                status = 128 + interrupted
            record.update(status="interrupted" if interrupted else "completed" if status == 0 else "failed",
                          exit_code=status, finished_at=datetime.now(timezone.utc).isoformat())
            save()
            print(f"Worker {record['status']} (exit {status}); results: {output}", flush=True)
            return status
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            record.update(status="failed", exit_code=2, error=str(error),
                          finished_at=datetime.now(timezone.utc).isoformat())
            save()
            raise
        finally:
            cleanup_errors = []
            try:
                try:
                    container = owned_container()
                    if container:
                        if container["State"]["Running"]:
                            docker("stop", "--signal", "SIGINT", "--timeout", "30", container["Id"],
                                   check=False, timeout=45)
                        docker("rm", "--force", container["Id"], check=False)
                        if owned_container() is not None:
                            raise RuntimeError("Worker container remains after cleanup")
                except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(str(error))
                try:
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(str(error))
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            if cleanup_errors:
                record.update(status="cleanup_failed", exit_code=2, cleanup_errors=cleanup_errors,
                              finished_at=datetime.now(timezone.utc).isoformat())
                save()
                raise RuntimeError("Worker cleanup failed: " + "; ".join(cleanup_errors))


def main():
    cli = parser()
    args = cli.parse_args()
    try:
        return run_worker(args)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        cli.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
