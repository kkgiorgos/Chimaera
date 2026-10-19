"""Advisory wall-time planning; never changes a requested timeout implicitly."""
import math


def wall_budget(config):
    simulated = config['warmup'] + config['duration'] + config['drain']
    stepped = config['mode'] != 'native_free'
    steps = math.ceil(simulated * 1e6 / config['interval_us']) if stepped else 0
    # Both controllers settle for 20 ms before and after a physics step.
    # Fortress world-statistics acknowledgements made each step take about
    # 100 ms on the benchmark host. This is an estimate, not an execution ETA;
    # detailed gem5 execution and host contention can add substantially more.
    floor = steps * .04
    estimate = steps * .1 + (60 if config['mode']=='chimaera' else 10) if stepped else simulated + 10
    recommended = min(86400, max(config['timeout'], math.ceil(estimate * 1.5 / 60) * 60))
    return dict(synchronization_steps=steps, settling_floor_seconds=floor,
                estimated_seconds=estimate, recommended_timeout=recommended)
