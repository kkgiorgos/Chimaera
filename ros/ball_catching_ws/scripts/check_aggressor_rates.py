#!/usr/bin/env python3
"""Verify fractional aggressor rates with short memory-only stream simulations."""
import argparse
import json
from pathlib import Path
import subprocess

from benchmark_metrics import read_stats
from run_memory_suite import digest, load_config, write_json


def check_rates(config, calibration, gem5_root, output):
    data = load_config(config)
    reference = json.loads(calibration.read_text())
    gem5 = gem5_root / 'build/X86/gem5.opt'
    if reference['pattern'] != 'stream' or reference['gem5_sha256'] != digest(gem5):
        raise ValueError('Stream calibration must match the simulator being checked')
    if output.exists():
        raise ValueError('Rate validation needs a fresh output directory')
    output.mkdir(parents=True)
    rates = []
    for pressure in (.5, .8, .9):
        directory = output / f'stream-{pressure:.0%}'
        directory.mkdir()
        offered = pressure * reference['sustainable_gbps']
        command = [str(gem5), '--outdir=' + str(directory),
                   str(Path(__file__).with_name('calibrate_memory.py')),
                   '--config', data['ramulator_config'], '--pattern', 'stream',
                   '--offered-gbps', str(offered)]
        for key in ('read_percent', 'stride', 'max_pending', 'window', 'seed'):
            command += ['--' + key.replace('_', '-'), str(data['aggressor'][key])]
        for key, value in data['calibration'].items():
            command += ['--' + key.replace('_', '-'), str(value)]
        with (directory / 'run.log').open('w') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        stats = read_stats(directory / 'stats.txt')
        seconds = stats['simTicks'] / stats['simFreq']
        achieved = stats['system.memory.aggressorBytes'] / seconds / 1e9
        error = abs(achieved / offered - 1)
        if error > .005:
            raise ValueError(f'Incorrect aggressor pacing at {pressure:.0%}: '
                             f'offered {offered:g}, accepted {achieved:g} GB/s')
        rates.append(dict(pressure=pressure, offered_gbps=offered, accepted_gbps=achieved,
                          relative_error=error))
    if not all(a['accepted_gbps'] < b['accepted_gbps'] for a, b in zip(rates, rates[1:])):
        raise ValueError('Distinct pressure levels must produce distinct accepted rates')
    result = dict(simulator_sha256=digest(gem5), rates=rates,
                  scope='Memory-only stream pacing; no guest boot or robot simulation', passed=True)
    write_json(output / 'validation.json', result)
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, required=True)
    cli.add_argument('--calibration', type=Path, required=True)
    cli.add_argument('--gem5-root', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    try:
        result = check_rates(args.config, args.calibration, args.gem5_root, args.output)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        cli.error(str(error))


if __name__ == '__main__':
    main()
