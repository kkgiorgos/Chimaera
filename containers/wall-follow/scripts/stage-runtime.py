"""Stage the SDK's runtime files without retaining its build layers."""

import json
from pathlib import Path
import shutil
import subprocess


root = Path("/runtime")
sdk = Path("/opt/chimaera")
profile = json.loads((sdk / "profile.json").read_text())
runtime = json.loads((sdk / "runtime-profile.json").read_text())


def copy(path):
    destination = root / path.relative_to("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir() and not path.is_symlink():
        shutil.copytree(path, destination, symlinks=True, dirs_exist_ok=True)
    elif path.is_symlink():
        destination.symlink_to(path.readlink())
    else:
        shutil.copy2(path, destination)


for prefix in (Path(f"/opt/ros/{profile['ros_distro']}"), sdk / "ros"):
    copy(prefix)
    staged = root / prefix.relative_to("/")
    for path in sorted(staged.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        if path.is_dir() and path.name in ("include", "cmake", "pkgconfig", "__pycache__"):
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in (".a", ".la", ".pyc"):
            path.unlink()
    for path in staged.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                is_elf = stream.read(4) == b"\x7fELF"
            if is_elf:
                subprocess.run(["strip", "--strip-unneeded", str(path)], check=True)

binary = sdk / "gem5/build/X86/gem5.opt"
copy(binary)
subprocess.run(["strip", "--strip-unneeded", str(root / binary.relative_to("/"))], check=True)
for name in ("profile.json", "runtime-profile.json", "build-manifest.json", "packages.tsv", "licenses"):
    copy(sdk / name)

owners = set()
for value in runtime["supplemental_paths"]:
    path = Path(value)
    copy(path)
    files = [path] if not path.is_dir() else [p for p in path.rglob("*") if p.is_file()]
    for file in files:
        owner = subprocess.check_output(["dpkg-query", "--search", str(file)], text=True).split(": ", 1)[0]
        owners.add(owner)
for package in owners:
    copy(Path("/usr/share/doc") / package.split(":")[0] / "copyright")
for path in Path("/usr/share/doc").glob("ros-humble-*/copyright"):
    copy(path)
(root / "opt/chimaera/supplemental-owners.json").write_text(json.dumps(sorted(owners), indent=2) + "\n")

for value in (
    "/etc/apt/sources.list.d/chimaera-gazebo.list", "/usr/share/keyrings/chimaera-gazebo.gpg",
    "/etc/ssl/certs", "/usr/share/ca-certificates",
):
    copy(Path(value))

packages = dict(line.split("\t") for line in (sdk / "packages.tsv").read_text().splitlines())
preferences = "".join(
    f"Package: {name}\nPin: version {version}\nPin-Priority: 1001\n\n"
    for name, version in sorted(packages.items())
) + "Package: *\nPin: release *\nPin-Priority: -1\n"
policy = root / "etc/apt/preferences.d/chimaera-sdk"
policy.parent.mkdir(parents=True, exist_ok=True)
policy.write_text(preferences)
