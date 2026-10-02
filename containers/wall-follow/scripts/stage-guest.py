"""Select the SDK's guest ROS dependency closure and exact system runtime."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(path, destination=None):
    destination = destination or payload / path.relative_to("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if not destination.is_symlink():
            destination.symlink_to(path.readlink())
    elif path.is_file():
        shutil.copy2(path, destination)


sdk = Path("/opt/chimaera")
profile = json.loads(Path("/guest-profile.json").read_text())
policy = json.loads(Path("/guest-policy.json").read_text())
if profile != json.loads((sdk / "profile.json").read_text()):
    raise RuntimeError("SDK stack profile differs from the requested guest profile")
distro = profile["ros_distro"]
prefix = Path(f"/opt/ros/{distro}")
payload = Path("/payload")
metadata = payload / "usr/local/share/chimaera"
metadata.mkdir(parents=True)
packages = dict(line.split("\t") for line in (sdk / "packages.tsv").read_text().splitlines())
records = subprocess.check_output([
    "dpkg-query", "--show", "--showformat=${binary:Package}\t${Depends}\n",
], text=True)
dependencies = dict(line.split("\t", 1) for line in records.splitlines())
selected = set()
system = set(policy["runtime_packages"])
pending = [f"ros-{distro}-{name}" for name in policy["ros_packages"]]
while pending:
    package = pending.pop()
    if package in selected:
        continue
    if package not in packages:
        raise RuntimeError(f"Guest ROS dependency is missing from SDK: {package}")
    selected.add(package)
    for group in dependencies[package].split(","):
        alternatives = [re.match(r"\s*([a-z0-9+.-]+)", part).group(1) for part in group.split("|") if part.strip()]
        installed = next((name for name in alternatives if name in packages), None)
        if installed and installed.startswith(f"ros-{distro}-"):
            pending.append(installed)
        elif installed and installed.startswith("python3") and not installed.endswith("-dev"):
            # ROS Debian metadata mixes runtime and build dependencies. Keep
            # Python modules for launch/setup; ELF inspection below discovers
            # system libraries without pulling their development packages.
            system.add(installed)
        elif not installed and any(name.startswith(f"ros-{distro}-") for name in alternatives):
            raise RuntimeError(f"No installed ROS dependency satisfies {package}: {group}")

for package in sorted(selected):
    for value in subprocess.check_output(["dpkg-query", "--listfiles", package], text=True).splitlines():
        path = Path(value)
        if path.is_relative_to(prefix):
            copy(path)
    copy(Path("/usr/share/doc") / package / "copyright")

from guest_session import stage_session

stage_session(sdk, payload)
copy(sdk / "gem5/util/m5/build/x86/out/m5", payload / "usr/local/bin/m5")
shutil.copytree(sdk / "licenses", metadata / "licenses", symlinks=True)
copy(sdk / "build-manifest.json", metadata / "sdk-build-manifest.json")

staged_ros = payload / prefix.relative_to("/")
staged_app = payload / "opt/chimaera/wall_follow/app"
for path in sorted([*staged_ros.rglob("*"), *staged_app.rglob("*")], reverse=True):
    if path.is_symlink():
        continue
    if path.is_dir() and path.name in ("include", "cmake", "pkgconfig", "__pycache__"):
        shutil.rmtree(path)
    elif path.is_file() and path.suffix in (".a", ".la", ".pyc"):
        path.unlink()

owners = {}
os.environ["LD_LIBRARY_PATH"] = str(prefix / "lib")
for path in sorted(payload.rglob("*")):
    if not path.is_file() or path.is_symlink():
        continue
    with path.open("rb") as stream:
        is_elf = stream.read(4) == b"\x7fELF"
    if not is_elf:
        continue
    # Inspect the matching source library search path before trimming ELF files.
    result = subprocess.run(["ldd", str(path)], capture_output=True, text=True)
    output = result.stdout + result.stderr
    if "not found" in output:
        raise RuntimeError(f"Unresolved SDK guest dependency: {path}\n{output}")
    for value in re.findall(r"(?:=>\s+|^\s*)(/[^\s]+)", output, re.MULTILINE):
        dependency = Path(value)
        if dependency.is_relative_to(prefix):
            relative = dependency.relative_to("/")
            if not (payload / relative).exists():
                raise RuntimeError(f"ROS closure misses {dependency} required by {path}")
            continue
        if value not in owners:
            candidates = [value, str(dependency.resolve())]
            if value.startswith(("/lib/", "/lib64/")):
                candidates.append("/usr" + value)
            elif value.startswith(("/usr/lib/", "/usr/lib64/")):
                candidates.append(value[4:])
            for candidate in dict.fromkeys(candidates):
                lookup = subprocess.run(["dpkg-query", "--search", candidate], capture_output=True, text=True)
                if lookup.returncode == 0:
                    found = {line.split(": ", 1)[0] for line in lookup.stdout.splitlines()}
                    if len(found) != 1:
                        raise RuntimeError(f"Ambiguous runtime owner for {value}: {found}")
                    owner = found.pop()
                    name = owner.split(":")[0]
                    if name.endswith("-dev") or "," in owner:
                        raise RuntimeError(f"Development runtime owner: {owner}")
                    owners[value] = name
                    break
            else:
                raise RuntimeError(f"No Debian package owns the runtime dependency {value}")
        system.add(owners[value])
    subprocess.run(["strip", "--strip-unneeded", str(path)], check=True)

(payload / "opt/chimaera").mkdir(parents=True, exist_ok=True)
for name in ("profile.json", "packages.tsv"):
    copy(sdk / name)
(payload / "opt/chimaera/runtime-profile.json").write_text(
    json.dumps({"runtime_packages": sorted(system)}, indent=2) + "\n")
preferences = "".join(
    f"Package: {name}\nPin: version {version}\nPin-Priority: 1001\n\n"
    for name, version in sorted(packages.items())
) + "Package: *\nPin: release *\nPin-Priority: -1\n"
apt = payload / "etc/apt/preferences.d/chimaera-sdk"
apt.parent.mkdir(parents=True)
apt.write_text(preferences)
# Some Python dependencies (for example catkin-pkg-modules) come from the ROS repo.
# Dereference the apt-source package's symlink to keep the runtime source complete.
source = payload / "etc/apt/sources.list.d/ros2.sources"
source.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2("/etc/apt/sources.list.d/ros2.sources", source)
shutil.copytree("/usr/share/keyrings", payload / "usr/share/keyrings", symlinks=True)

inventory = {
    "schema_version": 1,
    "sdk_manifest_sha256": sha256(sdk / "build-manifest.json"),
    "ros_packages": {name: packages[name] for name in sorted(selected)},
    "ros_dependencies": {name: dependencies[name] for name in sorted(selected)},
    "system_runtime_roots": sorted(system),
    "system_library_owners": owners,
    "files": {str(p.relative_to(payload)): {"sha256": sha256(p), "mode": stat.S_IMODE(p.stat().st_mode)}
              for p in sorted(payload.rglob("*")) if p.is_file() and not p.is_symlink()},
    "symlinks": {str(p.relative_to(payload)): str(p.readlink())
                 for p in sorted(payload.rglob("*")) if p.is_symlink()},
}
(metadata / "payload.json").write_text(json.dumps(inventory, indent=2) + "\n")
print(f"Staged {len(selected)} ROS packages and {len(system)} system runtime roots")
