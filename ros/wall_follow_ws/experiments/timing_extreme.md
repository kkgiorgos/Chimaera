# Extreme timing-CPU proof of concept

Run four cases crossing clock frequency with a cache-capacity profile. This is
a short sensitivity check: can the co-simulation expose a large hardware
change, and does the robot still receive commands regularly?

| Case | Clock | L1I per core | L1D per core | Shared L2 | Suite / case ID |
| --- | --- | --- | --- | --- | --- |
| Fast, generous caches | 3GHz | 64KiB | 64KiB | 2MiB | large / case_001_rep_01 |
| Slow, generous caches | 100MHz | 64KiB | 64KiB | 2MiB | large / case_002_rep_01 |
| Fast, tiny caches | 3GHz | 1KiB | 1KiB | 4KiB | tiny / case_001_rep_01 |
| Slow, tiny caches | 100MHz | 1KiB | 1KiB | 4KiB | tiny / case_002_rep_01 |

The frequency ratio is 30, each L1 capacity ratio is 64, and the L2 ratio is
512. These intentionally unrealistic endpoints should expose more sensitivity
than the existing 1GHz/3GHz and 8KiB/32KiB sweeps. Two JSON files keep the three
cache sizes together as one profile; independently sweeping all three sizes
would create sixteen cases rather than four.

All cases use two TimingSimpleCPU cores, MESI Two Level, 64-byte lines, 8-way
L1s and a 16-way L2, the existing DDR3-1600 memory and deployed guest image.
Tiny L1s have two sets each; the tiny L2 has four sets. They meet Ruby's
requirement of more than one set and preserve the corrected index bit of 6.
Associativity, cache latency parameters and memory settings stay fixed. In this
configuration,
`cpu_clock` sets the board clock, including its L1 controllers; interpret this
axis as a board-clock change rather than an isolated core-clock change.

The workload stays identical across all four cases: a 4-by-4-metre arena,
0.35m/s speed, 10Hz lidar and control, 720 beams, stride 1, no sensor noise,
and a 1ms physics step. The scan has four times as many beams as the completed
180-beam smoke run, increasing scan processing and transport work. The fitting
algorithm caps its pair hypotheses, so this does not imply a fourfold total
instruction count. The observation window is three simulated seconds after
the first recorded command, one repetition per case, with analysis warmup 0.
This retains the short startup transient; gem5 counters include the entire
cold-cache ROI from workbegin, which can be longer than the observation window.

TimingSimpleCPU waits for memory responses, so cache pressure can directly
delay instruction progress. MESI Two Level is inclusive: a very small shared
L2 can also force private-L1 evictions. These are reasons to expect a clear
cache effect, not measured results or a promised speedup.
See the [SimpleCPU documentation](https://www.gem5.org/documentation/general_docs/cpu_models/SimpleCPU)
and [MESI Two Level documentation](https://www.gem5.org/documentation/general_docs/ruby/MESI_Two_Level/).

Both runner dry-runs passed. A configuration-only check with the rebuilt
gem5 v25.1.0.1 binary constructed all four board graphs and verified the clock
periods, core counts, cache set counts and complete set mapping. It did not
instantiate simulation objects, load a guest or advance simulation time.
The generated world and controller configurations were also checked to be
identical across all four cases.

## Run when ready

The helper script runs both suites in order and then generates the combined
plots and dashboard. It sources ROS Humble and this workspace automatically.
From `ros/wall_follow_ws`:

```bash
./scripts/run_timing_extreme.sh
```

The host now coalesces pending clock updates at synchronization boundaries,
reducing clock traffic to at most 20Hz with this helper's 50ms interval. Scans
and control settings stay identical across the four cases. Use a fresh output
directory to compare all four with this policy; resuming the earlier suite
would reuse completed cases measured with the old clock traffic:

```bash
./scripts/run_timing_extreme.sh --output results/timing-extreme-clock-coalesced
```

The host package has been rebuilt for this change. The deployed guest remains
wire-compatible, so image deployment and a gem5 rebuild are unnecessary.

Results go in `results/timing-extreme-poc/large` and
`results/timing-extreme-poc/tiny`; the combined plots and `dashboard.html` go
in `results/timing-extreme-poc/comparison`. Open the HTML file in a browser.
Use `--output DIR` for a different results directory. The helper waits for
each suite to finish, continues after failed cases, compares completed cases,
and reports how many of the four cases are included. It returns a nonzero
status if any case or the comparison fails. Ctrl-C stops the sequence.

```bash
./scripts/run_timing_extreme.sh --dry-run
./scripts/run_timing_extreme.sh --resume
./scripts/run_timing_extreme.sh --plot-only
```

`--resume` skips completed cases and retries failed or missing ones before
rebuilding the comparison. `--plot-only` regenerates the plots and dashboard
without starting any simulations. All modes also accept `--output DIR`.

For manual execution of each suite, use these commands instead:

Run from `ros/wall_follow_ws` with ROS and the workspace sourced, using the
currently rebuilt gem5 and deployed guest. No rebuild or image deployment is
needed for these presets. Start with the large-cache suite; its first case is
the positive control. Execute the suites sequentially because they share
transport sockets. The commands below start simulations; preparing and
validating this design did not run them.

```bash
python3 scripts/run_gem5_experiments.py \
  --config experiments/timing_extreme_large.json \
  --architecture timing-extreme-poc \
  --gem5-root ../../gem5 --output results/timing-extreme-large \
  --interval-us 50000 --poll-us 10000 \
  --startup-timeout 600 --warmup 0 --keep-going --progress

python3 scripts/run_gem5_experiments.py \
  --config experiments/timing_extreme_tiny.json \
  --architecture timing-extreme-poc \
  --gem5-root ../../gem5 --output results/timing-extreme-tiny \
  --interval-us 50000 --poll-us 10000 \
  --startup-timeout 600 --warmup 0 --keep-going --progress
```

Append `--dry-run` to either command to print its validated two-case plan
without writing results or starting simulators. Use fresh output directories,
or append `--resume` to continue the exact same saved plan. `--keep-going`
retains a failed attempt and continues to the next case in that suite; the
suite still returns failure, so start the second suite even if the first
reported a failed case.

Each case allows two hours of host wall time, and simulator operations have
a 600-second timeout. These are limits, not runtime estimates. The corrected
five-second smoke run took about 26 minutes; extreme caches and a denser scan
can change host cost substantially, while a lower simulated clock can reduce
the amount of work executed per simulated second. Do not assume the slowest
guest hardware will take the longest host time.

## Compare the completed cases

```bash
python3 scripts/compare_experiments.py \
  results/timing-extreme-large results/timing-extreme-tiny \
  --output comparison/timing-extreme-poc --warmup 0
```

This produces trajectory/error plots, co-simulation timing plots, an interactive
dashboard, a configuration table and summaries containing gem5 counters. For
the hardware comparison, inspect each attempt's `gem5/stats.txt` alongside
`gem5_summary.json`:

| Measurement | What to compare |
| --- | --- |
| L1I, L1D, L2 demand miss rates | Sum misses across cores/controllers, divide by the corresponding summed accesses. Expect much higher rates with tiny caches, especially L2. |
| Demand misses per 1,000 instructions (MPKI) | `1000 * summed demand misses / simInsts`, separately for each level. Prefer this to raw misses when completed instruction counts differ. |
| Instructions per simulated second | `simInsts / simSeconds`. Measures whole-guest progress; changing clocks, cache stalls and OS activity can all affect it. |
| CPU idle fraction and IPC excluding idle cycles | Inspect both ROI cores. Whole-ROI IPC includes idle time and can hide an overloaded case; neither metric isolates the controller. |
| Observed command cadence | Target is 10Hz, a 100ms interval. Inspect `samples.csv` for long `dt_sim` gaps and commands arriving in batches at one timestamp, alongside the observed-rate summary. Receipts are quantized by the 50ms synchronization interval. |
| Robot motion | Compare wall-error curves, path length and time-weighted RMSE. Slow/tiny hardware may cause worse tracking, stopped motion or a command timeout; this is a hypothesis to test. |
| Host simulation cost | Compare wall seconds per simulated second, separately from command cadence in simulated time. |

Compare fast versus slow within each cache profile to see the clock effect;
compare large versus tiny at each clock to see the cache-profile effect. All
three cache capacities move together, so these cases cannot attribute an
effect to a specific cache level. The crossed cases reveal whether generous
caches help more at one clock frequency. Report the actual ROI duration and
the time of the first command, since delayed guest startup can make ROI lengths
differ even when the recorded robot windows are equal.

A useful PoC outcome is a substantial change in normalized cache metrics or
guest progress. If every case still delivers about 10Hz and tracks similarly,
the controller meets its workload at all four endpoints; hardware sensitivity
can still appear in cache misses and CPU occupancy. If tracking degrades,
check whether it coincides with longer observed command gaps or stopped
commands. The recorder does not expose guest computation time, guest scan age
or internal controller state, so it cannot prove individual deadline misses
or guest scan freshness.

The collector currently fails after a gap of more than one simulated second
between commands, once collection has started. Inspect such a failure's logs
and partial samples as a possible overload result. A host wall timeout,
simulator-operation timeout, missing first command or guest boot error needs
its own diagnosis; it is not automatically a controller deadline failure.
Default comparisons skip failed attempts. Add `--include-incomplete` only for
diagnosis when partial samples exist, and keep those separate from successful
cases.

Three seconds and one repetition will not establish steady-state performance
or corner-following quality. After this check, repeat only the informative
cases with a longer observation window and at least three repetitions.
