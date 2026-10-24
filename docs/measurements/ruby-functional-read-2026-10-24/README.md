# Ruby functional-read recovery, 24 October 2026

Preparation `57cfcb8c11ae4d41b794d322b717f2fb` stopped at 0.116 simulated
seconds on the configured Timing CPUs. gem5 reported:

```
src/mem/ruby/system/RubyPort.cc:463: fatal:
Ruby functional read failed for address 0x1af5ec18
```

An independent, byte-checking memory workload reproduced this failure class
without Linux, ROS or Gazebo. Two MemTest requestors interleave functional
reads/writes with timing requests while false-sharing cache lines. Ruby's
MESI_Two_Level directory classified both an outstanding memory fetch (`IM`)
and a writeback awaiting acknowledgement (`MI`) as `Busy`. When the caches had
no readable copy and the message buffers carried no data, Ruby could not find
a functional-read source even though memory or its writeback queue held the
current bytes.

Both directory states now expose `Maybe_Stale` for functional lookup. Ruby
continues to prefer readable caches and in-flight data before consulting the
directory's memory interface. The existing functional memory read checks its
writeback request queue before reading memory. Coherence transitions, timing
latencies, guest hardware, copying mode and failure checks are unchanged.

The regression fails on the old simulator. Its trace shows a directory fetch
with all three relevant controllers busy. A larger test also demonstrates a
writeback transition with no readable cache/message copy. Correcting only the
fetch state was insufficient; the retained final tests exercise both paths.

Validation commands use `gem5.opt`, not ordinary Python:

```sh
gem5/build/X86/gem5.opt --outdir=/tmp/ruby-functional \
  ros/ball_catching_ws/tests/verify_ruby_functional.py --gem5-root gem5

gem5/build/X86/gem5.opt --outdir=/tmp/ruby-functional-lpddr5 \
  ros/ball_catching_ws/tests/verify_ruby_functional.py --gem5-root gem5 \
  --loads 100000 --testers 4 \
  --memory gem5/ext/ramulator2/configs/lpddr5-6400-orin.json
```

Final checks passed:

- Two requestors, 25% functional accesses, standard DRAM, 10,000-load limit.
- Four requestors, 50% functional accesses, standard DRAM, 100,000-load limit.
- Four requestors, 25% functional accesses, LPDDR5/Ramulator, 100,000-load limit.
- Dirty MESI/Ramulator checkpoint capture: all 131,072 expected 64-bit values
  matched the saved backing RAM.
- Cold-cache checkpoint restore: every value remained correct.

The load limit ends a run when its leading requestor reaches that count.
MemTest validates the bytes returned by successful reads against reference
values; functional errors are not ignored. The wrapper rejects an early tick
limit or any other unexpected exit.

A separate full-system diagnostic reuses the failed preparation's exact guest
bundle, disk, kernel and detailed Timing configuration, replacing the simulator
and regenerating its memory calibration. This diagnostic must be distinguished
from a new production experiment and from a completed readiness checkpoint.
See `validation.json` for its final observed progress and execution hashes.

The diagnostic reached 0.194 simulated seconds with no fatal or panic, beyond
the previous 0.116-second failure. It was stopped to launch the supervised
replacement; a complete readiness checkpoint and catching results remain pending.
`evidence.tar.gz` retains the failure, reproducer traces and validation logs.
