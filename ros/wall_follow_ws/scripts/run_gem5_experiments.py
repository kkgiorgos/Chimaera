#!/usr/bin/env python3
"""Run the automated suite using gem5 (see run_experiments.py --help)."""
import sys
from run_experiments import main

if __name__ == '__main__':
    sys.argv.insert(1, '--gem5')
    sys.exit(main())
