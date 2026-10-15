# Two-throw memory interference example

Configuration: [memory_contention_two_throws.json](memory_contention_two_throws.json).
Run commands below from the repository root.

| Throw | Launch position (m) | Direction (normalized by the simulator) | Speed |
| --- | --- | --- | --- |
| `loft-3` | `[1.8, 0.04, 3]` | `[-1, 0, 2]` | 3 m/s |
| `loft-5` | `[4.36, 0.04, 3]` | `[-1, 0, 1]` | 5 m/s |

Each throw runs once without interference, then once per stream, 4096-byte
stride, and seeded random pattern at 50%, 80%, and 90% pressure. This gives
20 independent one-throw trials; each restarts the simulation. Pressure is
relative to each pattern's separately calibrated sustainable bandwidth.
The synthetic aggressor uses 80% reads, a 256 MiB window, seed 1, and up to
512 pending requests with backpressure. It runs through readiness and the throw.

| Cases | Throw | Interference |
| --- | --- | --- |
| `case-000` | 3 m/s | Baseline |
| `case-001`–`case-003` | 3 m/s | Stream: 50%, 80%, 90% |
| `case-004`–`case-006` | 3 m/s | Stride: 50%, 80%, 90% |
| `case-007`–`case-009` | 3 m/s | Random: 50%, 80%, 90% |
| `case-010` | 5 m/s | Baseline |
| `case-011`–`case-013` | 5 m/s | Stream: 50%, 80%, 90% |
| `case-014`–`case-016` | 5 m/s | Stride: 50%, 80%, 90% |
| `case-017`–`case-019` | 5 m/s | Random: 50%, 80%, 90% |

The CPU remains four x86 Timing cores at 3 GHz with private 32 KiB L1I/L1D
caches and an 8 MiB shared last-level cache. The memory interface approximates
Jetson AGX Orin 64GB: 256-bit LPDDR5-6400, 204.8 GB/s theoretical peak and
64 GiB modeled capacity. The guest exposes 3 GiB. This is a memory-interface
approximation; the CPU, cache hierarchy, GPU, fabric/QoS and power management
are not Orin models. See the [memory guide](../../../docs/ball-catching-memory-contention.md)
for model assumptions and build prerequisites.

Robot settings use 480×360 stereo at 30 Hz, 30 Hz planning, 250 Hz effort
control, 1 kHz joint feedback and 0.5 ms physics/exchange intervals. Scoring is
1.5 simulated seconds after launch, with zero extra ready hold. The 5 m/s
launch geometry matches the successful host demonstration, but that demonstration
used 1280×960 stereo at 90 Hz. Run the native preflight below to check both
throws with this experiment's camera and coupling settings. Native results do
not qualify the gem5 guest or measure memory interference.

Inspect the plan without starting simulation:

```bash
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --config ros/ball_catching_ws/experiments/memory_contention_two_throws.json \
  --dry-run
```

Run the two native baseline throws only:

```bash
source /opt/ros/humble/setup.bash
source ros/ball_catching_ws/install/local_setup.bash
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --config ros/ball_catching_ws/experiments/memory_contention_two_throws.json \
  --native-preflight --output ros/ball_catching_ws/results/two-throws-native
```

Prepare the guest experiment without starting calibration or simulations:

```bash
source /opt/ros/humble/setup.bash
source ros/chimaera_ros_ws/install/local_setup.bash
source ros/ball_catching_ws/install/local_setup.bash
python3 ros/ball_catching_ws/scripts/run_memory_suite.py \
  --config ros/ball_catching_ws/experiments/memory_contention_two_throws.json \
  --prepare-only --output ros/ball_catching_ws/results/two-throws-memory
```

When ready, run the prepared suite serially (one native Chimaera session at a
time). This starts memory calibration and all 20 trials. Each trial has a
12-hour wall-time limit; rerunning the launcher resumes completed work.

```bash
bash ros/ball_catching_ws/results/two-throws-memory/run.sh
```

Monitor in another terminal:

```bash
python3 ros/ball_catching_ws/scripts/watch_memory_suite.py \
  ros/ball_catching_ws/results/two-throws-memory
```

The suite saves `dashboard.html` under its output directory.
