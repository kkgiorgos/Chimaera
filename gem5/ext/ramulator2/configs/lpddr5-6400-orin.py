"""AGX Orin-like memory interface; export with the pinned Ramulator Python DSL.

NVIDIA specifies 64 GB, 256-bit LPDDR5 and 204.8 GB/s. Model eight ganged
32-bit channels (two x16 devices per rank), two ranks per channel, at 6400
MT/s. Ganging keeps each BL16 transaction at the gem5 cache-line size (64 B).
This is a generic JEDEC memory/controller approximation, not NVIDIA's MC,
interconnect, QoS, power modes, or CPU model.

PYTHONPATH=gem5/ext/ramulator2/ramulator2/python python3 -m ramulator export \
    gem5/ext/ramulator2/configs/lpddr5-6400-orin.py -f json \
    -o gem5/ext/ramulator2/configs/lpddr5-6400-orin.json
"""
import ramulator

controllers = [ramulator.controller.LPDDR5(
    dram=ramulator.dram.LPDDR5(org_preset="LPDDR5_16Gb_x16",
                            timing_preset="LPDDR5_6400", channel_width=32, rank=2),
    scheduler=ramulator.scheduler.FRFCFS(),
    refresh_manager=ramulator.refresh_manager.AllBank(),
    row_policy=ramulator.row_policy.Open(),
    addr_mapper=ramulator.addr_mapper.RoBaRaCoCh(),
) for _ in range(8)]

simulation = ramulator.Simulation(
    ramulator.frontend.External(clock_ratio=1),
    ramulator.memory_system.GenericDRAM(
        clock_ratio=1, controllers=controllers,
        channel_mapper=ramulator.channel_mapper.CacheLineInterleave()),
)
