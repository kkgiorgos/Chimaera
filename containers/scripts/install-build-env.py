"""Install the repositories and build packages defined by the stack profile."""

import hashlib
import json
import os
from pathlib import Path
import subprocess


def download(spec, destination):
    subprocess.run([
        "curl", "--fail", "--show-error", "--location", "--retry", "3",
        "--output", str(destination), spec["url"],
    ], check=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if digest != spec["sha256"]:
        raise ValueError(f"Checksum mismatch for {spec['url']}: {digest}")


profile = json.loads(Path("/opt/chimaera/profile.json").read_text())
os.environ["DEBIAN_FRONTEND"] = "noninteractive"
ros_source = Path("/tmp/ros2-apt-source.deb")
download(profile["ros_apt_source"], ros_source)
subprocess.run(["dpkg", "--install", str(ros_source)], check=True)
ros_source.unlink()
key = Path("/usr/share/keyrings/chimaera-gazebo.gpg")
download(profile["gazebo_key"], key)
Path("/etc/apt/sources.list.d/chimaera-gazebo.list").write_text(
    "deb [arch=amd64 signed-by=/usr/share/keyrings/chimaera-gazebo.gpg] "
    f"https://packages.osrfoundation.org/gazebo/ubuntu-stable {profile['ubuntu_codename']} main\n"
)
subprocess.run(["apt-get", "update"], check=True)
subprocess.run([
    "apt-get", "install", "--yes", "--no-install-recommends", *profile["build_packages"],
], check=True)
packages = subprocess.check_output([
    "dpkg-query", "--show", "--showformat=${binary:Package}\t${Version}\n",
], text=True)
Path("/opt/chimaera/packages.tsv").write_text(packages)
