#!/usr/bin/env python3
"""Run the single qualified, unpressured ball-catching case through a selected middleware."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ros/ball_catching_ws/scripts"))
from run_memory_suite import gem5_trial, load_config, plan, prepare_bundle, routes, write_json
import run_memory_suite


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--gem5-root", type=Path, default=Path("/opt/chimaera/gem5"))
    parser.add_argument("--cpu-type", choices=("kvm", "timing", "o3"), default="kvm")
    parser.add_argument("--middleware", choices=("bridge", "fastdds"), default="fastdds")
    parser.add_argument("--backend", choices=("m5", "dma"), default="m5")
    parser.add_argument("--dma-module", type=Path)
    parser.add_argument("--clock-probe", action="store_true",
                        help="Include an optional diagnostic process; exclude from final measurements")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / "ros/ball_catching_ws/experiments/memory_contention_timing_validation.json"
    raw = json.loads(source.read_text())
    raw["ramulator_config"] = str((source.parent / raw["ramulator_config"]).resolve())
    raw["hardware"]["cpu_type"] = args.cpu_type
    raw["fixed"].update(transport_backend=args.backend, middleware_transport=args.middleware,
                         image_diagnostics=False,
                         dma_module=str(args.dma_module.resolve()) if args.dma_module else "")
    if args.backend == "dma" and not args.dma_module:
        parser.error("DMA validation requires the module built for the selected kernel")
    write_json(output / "experiment.json", raw)
    data = load_config(output / "experiment.json")
    points = plan(data)
    if len(points) != 1 or points[0]["pressure"]:
        raise ValueError("Acceptance configuration must contain one unpressured baseline")
    write_json(output / "routes.json", routes())
    if args.clock_probe:
        original_session = run_memory_suite.session

        def diagnostic_session(*values, **kwargs):
            spec = original_session(*values, **kwargs)
            spec['deploy']['install_trees'].append(dict(
                source=str(ROOT / 'middleware'), destination='diagnostics'))
            spec['guest']['processes'].append(dict(name='clock_probe', command=[
                'python3', run_memory_suite.GUEST_ROOT + '/diagnostics/clock_probe.py']))
            return spec

        run_memory_suite.session = diagnostic_session
    bundle = prepare_bundle(output, data, points)
    artifacts = dict(image=args.image, kernel=args.kernel, guest_bundle=bundle,
                     gem5=args.gem5_root / 'build/X86/gem5.opt')
    if args.dma_module:
        artifacts['dma_module'] = args.dma_module
    write_json(output / 'provenance.json', dict(
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                text=True).strip(),
        cpu_type=args.cpu_type, middleware=args.middleware, backend=args.backend,
        clock_probe=args.clock_probe,
        artifacts={name: dict(path=str(path.resolve()), sha256=sha256(path))
                   for name, path in artifacts.items()}))
    (output / 'source.patch').write_bytes(subprocess.check_output(
        ['git', 'diff', 'HEAD', '--binary'], cwd=ROOT))
    artifacts = SimpleNamespace(image=args.image, kernel=args.kernel, gem5_root=args.gem5_root)
    gem5_trial(output / "baseline", output, data, points[0], artifacts, {}, bundle)
    result = json.loads((output / "baseline/result.json").read_text())
    print(json.dumps(result, indent=2))
    return 0 if result.get("caught", result.get("success", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
