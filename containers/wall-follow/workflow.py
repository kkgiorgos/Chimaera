"""Shared setup for the wall-follow suite and development commands."""
from pathlib import Path
import os
import shlex
import subprocess
import sys

DIRECTORY = Path(__file__).resolve().parent


def run_script(path, *arguments):
    command = [sys.executable, str(path), *map(str, arguments)]
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)


def prepare(image, overlay, jobs, rebuild_image=False):
    present = subprocess.run(['docker', 'image', 'inspect', image],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rebuild_image or present.returncode:
        run_script(DIRECTORY.parent / 'build.py', '--tag', image, '--jobs', jobs)
    run_script(DIRECTORY / 'build-overlay.py', '--image', image, '--output', overlay, '--jobs', jobs)


def report_command(image, output, warmup):
    """Use the image's analysis dependencies; the host only needs stdlib Python."""
    workspace = DIRECTORY.parents[1] / 'ros/wall_follow_ws'
    return ['docker', 'run', '--rm', '--init', '--network', 'none', '--read-only',
            '--user', f'{os.getuid()}:{os.getgid()}',
            '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777',
            '--mount', f'type=bind,source={workspace},target=/app/ros/wall_follow_ws,readonly',
            '--mount', f'type=bind,source={DIRECTORY / "compare.py"},target=/app/containers/wall-follow/compare.py,readonly',
            '--mount', f'type=bind,source={output},target=/output',
            image, 'python3', '/app/containers/wall-follow/compare.py',
            '--output', '/output', '--report', '/output/comparison', '--warmup', str(warmup)]
