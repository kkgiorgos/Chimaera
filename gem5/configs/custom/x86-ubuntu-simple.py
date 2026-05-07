# Copyright (c) 2021-2025 The Regents of the University of California
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""

This script shows an example of running a full system Ubuntu boot simulation
using the gem5 library. This simulation boots Ubuntu 24.04 using 2 KVM CPU
cores. The simulation then switches to 2 Timing CPU cores for the rest of the
simulation.

Usage
-----

```
scons build/ALL/gem5.opt
./build/ALL/gem5.opt configs/example/gem5_library/x86-ubuntu-run-with-kvm.py
```
"""

from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import (
    SimpleSwitchableProcessor,
)
from gem5.isas import ISA
from gem5.resources.resource import obtain_resource
from gem5.resources.resource import KernelResource
from gem5.resources.resource import DiskImageResource
from gem5.simulate.exit_handler import ExitHandler
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator
from gem5.utils.override import overrides
from gem5.utils.requires import requires

# This checks if the host system supports KVM. It also checks if the gem5
# binary is compiled to include the MESI_Two_Level cache coherence protocol.
requires(
    coherence_protocol_required=CoherenceProtocol.MESI_TWO_LEVEL,
    kvm_required=True,
)

from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import (
    MESITwoLevelCacheHierarchy,
)

import m5

# Here we set up a MESI Two Level Cache Hierarchy.
cache_hierarchy = MESITwoLevelCacheHierarchy(
    l1d_size="16KiB",
    l1d_assoc=8,
    l1i_size="16KiB",
    l1i_assoc=8,
    l2_size="256KiB",
    l2_assoc=16,
    num_l2_banks=1,
)

# Set up the system memory.
memory = SingleChannelDDR3_1600(size="3GiB")

# Here we set up the processor. This is a special switchable processor in which
# a starting core type and a switch core type must be specified. Once a
# configuration is instantiated a user may call `processor.switch()` or
# `simulator.switch_processor()`, if using a hypercall exit handler, to switch
# from the starting core types to the switch core types. In this simulation
# we start with KVM cores to simulate the OS boot, then switch to the Timing
# cores for the command we wish to run after boot.

processor = SimpleSwitchableProcessor(
    starting_core_type=CPUTypes.KVM,
    switch_core_type=CPUTypes.TIMING,
    isa=ISA.X86,
    num_cores=2,
)

# Here we set up the board. The X86Board allows for FS mode (full system) or
# SE mode (syscall emulation) X86 simulations.

board = X86Board(
    clk_freq="3GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

board.set_kernel_disk_workload(
    kernel=KernelResource(local_path="/home/kkgiorgos/University/Diploma/Chimaera/gem5/resources/vmlinux-x86-ubuntu-6.8.0-52-generic"),
    disk_image=DiskImageResource(local_path="/home/kkgiorgos/University/Diploma/Chimaera/gem5/resources/ubuntu-24.04-test.img"),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=/dev/sda2"
    ],
    readfile_contents="""#!/bin/bash
#!/bin/sh
echo "Hello from inside the simulated system!"
/bin/bash
"""
)

is_started = False
is_finished = False

def on_workbegin():
    global is_started
    print("[host] ROI begin reached")
    print("[host] Switching processor to detailed model")
    processor.switch()

    # You can reset stats here to isolate ROI stats.
    m5.stats.reset()
    is_started = True
    yield True

def on_workend():
    global is_finished
    print("[host] ROI end reached")
    print("[host] Dumping ROI stats")
    m5.stats.dump()
    is_finished = True
    yield True

def on_exit():
    # Ubuntu/systemd/full boot may generate multiple EXIT events depending on workload path.
    cause = simulator.get_last_exit_event_cause()
    tick = simulator.get_current_tick()
    print(f"[host] EXIT event at tick {tick}: {cause}")
    yield False


simulator = Simulator(
    board=board,
    on_exit_event={
        ExitEvent.WORKBEGIN: on_workbegin(),
        ExitEvent.WORKEND: on_workend(),
        ExitEvent.EXIT: on_exit(),
        # ExitEvent.MAX_TICK: on_max_tick(),
    },
)

# Run until workbegin
simulator.run()

# STEP_TICKS = 1_000_000_000_000
# step_count = 0
#
# while not is_started:
#     simulator.run(max_ticks=STEP_TICKS)
#     step_count += 1
#     print(f"Step {step_count} done")
#
# print("Started")
#
# while not is_finished:
#     simulator.run(max_ticks=STEP_TICKS)
#     step_count += 1
#     print(f"Step {step_count} done")
#
# print("Finished")

# We acknowlwdge the user that the simulation has ended.
print(
    "Exiting @ tick {} because {}.".format(
        simulator.get_current_tick(),
        simulator.get_last_exit_event_cause(),
    )
)
