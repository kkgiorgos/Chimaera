"""gem5 regression: coherent functional accesses during concurrent MESI misses.

Run with gem5.opt, not ordinary Python. Two MemTest requestors false-share lines
and interleave timing requests with functional reads/writes. MemTest checks each
returned byte against a reference value; failed functional reads are not ignored.
The uncorrected MESI directory fails before 10,000 loads per requestor.
"""
import argparse
import runpy
import sys
from pathlib import Path

from m5.defines import buildEnv

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--gem5-root", type=Path, required=True)
parser.add_argument("--loads", type=int, default=10000)
parser.add_argument("--testers", type=int, default=2)
parser.add_argument("--functional", type=int, default=25)
parser.add_argument(
    "--memory", type=Path,
    help="Expanded Ramulator configuration; default uses DRAM",
)
args = parser.parse_args()
if buildEnv["PROTOCOL"] != "MESI_Two_Level":
    raise RuntimeError("Regression requires MESI_Two_Level")
if args.loads <= 0 or not 2 <= args.testers <= 8 or not 1 <= args.functional <= 99:
    raise ValueError("Use positive loads, 2–8 testers and 1–99 percent functional requests")
config = args.gem5_root.resolve() / "configs/example/ruby_mem_test.py"
sys.path.insert(0, str(config.parent.parent))
sys.argv = [
    str(config),
    "--num-cpus=" + str(args.testers),
    "--functional=" + str(args.functional),
    "--maxloads=" + str(args.loads),
    "--progress=" + str(args.loads),
    "--mem-size=64MiB",
    "--abs-max-tick=1000000000",
]
if args.memory:
    from m5.objects import Ramulator2
    Ramulator2.ramulator_config = args.memory.read_text()
    Ramulator2.aggressor_enabled = False
    sys.argv += ["--mem-type=Ramulator2"]
state = runpy.run_path(str(config), run_name="__m5_main__")
if state["exit_event"].getCause() != "maximum number of loads reached":
    raise RuntimeError("Functional memory regression did not complete its load count")
print("RUBY_FUNCTIONAL_MEMORY_OK", flush=True)
