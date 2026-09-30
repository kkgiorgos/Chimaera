"""Install only runtime packages, at the SDK's exact system library versions."""

import json
import os
from pathlib import Path
import subprocess


root = Path("/opt/chimaera")
sdk = dict(line.split("\t") for line in (root / "packages.tsv").read_text().splitlines())
versions = {name.split(":")[0]: version for name, version in sdk.items()}
runtime = json.loads((root / "runtime-profile.json").read_text())
os.environ["DEBIAN_FRONTEND"] = "noninteractive"


def installed():
    output = subprocess.check_output([
        "dpkg-query", "--show", "--showformat=${binary:Package}\t${Version}\n",
    ], text=True)
    return dict(line.split("\t") for line in output.splitlines())


requests = {f"{name}={versions[name]}" for name in runtime["runtime_packages"]}
for name, version in installed().items():
    if sdk[name] != version:
        requests.add(f"{name}={sdk[name]}")
subprocess.run(["apt-get", "install", "--yes", "--no-install-recommends", *sorted(requests)], check=True)
resolved = installed()
policy = subprocess.run(["apt-cache", "policy"], capture_output=True, text=True, check=True)
if policy.stderr:
    raise RuntimeError(f"Invalid runtime package policy: {policy.stderr}")
for name, version in resolved.items():
    if sdk.get(name) != version:
        raise RuntimeError(f"Runtime package differs from SDK: {name}={version}; SDK={sdk.get(name)}")
    if name.split(":")[0].endswith("-dev"):
        raise RuntimeError(f"Development package in runtime: {name}")
(root / "runtime-packages.tsv").write_text("".join(f"{name}\t{version}\n" for name, version in sorted(resolved.items())))
