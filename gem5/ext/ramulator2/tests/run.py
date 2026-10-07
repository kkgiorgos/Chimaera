"""Run integration regressions against a Ramulator-enabled gem5 binary."""

import argparse
import re
import subprocess
import tempfile
from pathlib import Path


def request_counts(path):
    text = path.read_text()
    return tuple(
        int(re.search(rf"total_num_{kind}_requests: (\d+)", text)[1])
        for kind in ("read", "write")
    )


def main():
    gem5_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gem5", type=Path, default=gem5_root / "build/X86/gem5.opt"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-system", action="store_true")
    parser.add_argument(
        "--case", action="append",
        choices=("configuration", "single", "vector", "mode-switch",
                 "timing-se", "o3-se", "full-system"),
        help="Run only selected cases (repeatable)",
    )
    parser.add_argument(
        "--kernel", type=Path,
        default=gem5_root / "resources/x86-linux-kernel-5.15.180",
    )
    parser.add_argument(
        "--image", type=Path,
        default=gem5_root / "resources/x86-ubuntu-22.04-ros-humble.img",
    )
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix="ramulator21-"))
    output.mkdir(parents=True, exist_ok=True)
    config = gem5_root / "ext/ramulator2/configs/ddr4-2400.json"
    tests = Path(__file__).resolve().parent
    binary = gem5_root / "tests/test-progs/hello/bin/x86/linux/hello"
    example = gem5_root / "configs/example/gem5_library/ramulator2.py"
    cases = [
        ("configuration", tests / "configuration.py", []),
        ("single", tests / "memory.py", []),
        ("vector", tests / "memory.py", ["--vector"]),
        (
            "mode-switch",
            tests / "mode_switch.py",
            ["--binary", str(binary)],
        ),
        ("timing-se", example, ["--binary", str(binary)]),
        (
            "o3-se",
            example,
            ["--binary", str(binary), "--cpu-type", "o3"],
        ),
    ]
    if args.full_system or "full-system" in (args.case or []):
        cases.append((
            "full-system", tests / "full_system.py",
            ["--kernel", str(args.kernel.resolve()),
             "--image", str(args.image.resolve())],
        ))
    if args.case:
        cases = [case for case in cases if case[0] in args.case]
    for name, script, extra in cases:
        directory = output / name
        directory.mkdir(exist_ok=True)
        with (directory / "run.log").open("w") as log:
            result = subprocess.run(
                [
                    str(args.gem5.resolve()),
                    "--outdir=" + str(directory.resolve()),
                    str(script),
                    "--config",
                    str(config),
                    *extra,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300 if name == "full-system" else 120,
            )
        if result.returncode:
            raise RuntimeError(
                f"{name} failed ({result.returncode}): "
                f"{directory / 'run.log'}"
            )
        if name == "configuration":
            assert "RAMULATOR_CONFIGURATION_OK" in (
                directory / "run.log"
            ).read_text()
            print("PASS configuration", flush=True)
            continue
        stats = list(directory.glob("*.ramulator_stats.yaml"))
        assert len(stats) == 1, stats
        reads, writes = request_counts(stats[0])
        assert reads > 0, (name, reads, writes)
        if name in ("single", "vector"):
            assert writes > 0, (name, reads, writes)
            assert len(list(directory.glob("*.ramulator_stats.*.yaml"))) >= 2
            assert "RAMULATOR_MEMORY_OK" in (directory / "run.log").read_text()
        if name == "mode-switch":
            assert "RAMULATOR_MODE_SWITCH_OK" in (
                directory / "run.log"
            ).read_text()
        if name == "full-system":
            assert "RAMULATOR_FULL_SYSTEM_OK" in (
                directory / "run.log"
            ).read_text()
        print(f"PASS {name}: {reads} reads, {writes} writes", flush=True)
    print(f"Results: {output.resolve()}")


if __name__ == "__main__":
    main()
