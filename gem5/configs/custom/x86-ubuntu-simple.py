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

"""Boot the Ubuntu 22.04/ROS Humble image for interactive guest testing.

Run from the repository root:
    gem5/build/X86/gem5.opt gem5/configs/custom/x86-ubuntu-simple.py

Connect to the serial terminal on the port printed by gem5 (normally 3456).
KVM boots the guest without perf counters. A guest m5 workbegin switches to
Timing CPUs and resets statistics; m5 workend dumps statistics and stops.
Ordinary m5 exit events are logged and ignored to keep the guest interactive.
"""

from pathlib import Path

from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import (
    SimpleSwitchableProcessor,
)
from gem5.isas import ISA
from gem5.resources.resource import KernelResource
from gem5.resources.resource import DiskImageResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator
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

# KVM is used for booting, not for collecting hardware performance counters.
# Avoid perf_event_open permission failures on hosts with restrictive perf policy.
for core in processor.get_cores():
    core.get_simobject().usePerf = False

# Here we set up the board. The X86Board allows for FS mode (full system) or
# SE mode (syscall emulation) X86 simulations.

board = X86Board(
    clk_freq="3GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

resources = Path(__file__).resolve().parents[2] / "resources"
kernel_path = resources / "x86-linux-kernel-5.15.180"
disk_path = resources / "x86-ubuntu-22.04-ros-humble.img"
for resource_path in (kernel_path, disk_path):
    if not resource_path.is_file():
        raise FileNotFoundError(f"Missing guest resource: {resource_path}")

board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=str(kernel_path)),
    disk_image=DiskImageResource(local_path=str(disk_path)),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=/dev/sda2",
        "mce=off",
    ],
    readfile_contents="""#!/bin/bash
echo "Hello from inside the simulated system!"
/bin/bash
"""
)

def on_workbegin():
    switched = False
    while True:
        print("[host] ROI begin reached")
        if not switched:
            print("[host] Switching processor to Timing CPUs")
            processor.switch()
            switched = True
        m5.stats.reset()
        yield False


def on_workend():
    while True:
        print("[host] ROI end reached; dumping statistics")
        m5.stats.dump()
        yield True


def on_exit():
    # Keep handling every exit; an exhausted generator falls back to gem5's
    # default EXIT handler, which would stop the interactive simulation.
    while True:
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
    },
)

# Continue through boot and workbegin; workend (or another stopping event)
# returns control to Python. WORKBEGIN itself does not stop simulator.run().
simulator.run()

print(
    "Exiting @ tick {} because {}.".format(
        simulator.get_current_tick(),
        simulator.get_last_exit_event_cause(),
    )
)
