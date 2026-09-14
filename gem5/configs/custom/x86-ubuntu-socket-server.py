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
This is a socket-controlled version of configs/custom/x86-ubuntu-simple.py.

The config opens a Unix stream socket and waits for commands from the host.
Supported line commands are:

    ADVANCE_SECONDS <seconds>
    STEP_SECONDS <seconds>
    ADVANCE_TICKS <ticks>
    STEP_TICKS <ticks>
    ADVANCE <ticks>
    STATUS
    DONE
    QUIT

JSON commands are also accepted, for example:

    {"cmd": "advance_seconds", "seconds": 0.001}
    {"cmd": "status"}

Every response is one JSON object followed by a newline. The default socket
path matches the host time bridge: /tmp/chimaera_time.sock.
"""

import argparse
import json
import socket
from pathlib import Path

from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import (
    MESITwoLevelCacheHierarchy,
)
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import (
    SimpleSwitchableProcessor,
)
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource
from gem5.resources.resource import KernelResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

import m5
import m5.ticks


parser = argparse.ArgumentParser()
parser.add_argument(
    "--socket-path",
    default="/tmp/chimaera_time.sock",
    help="Unix socket path used for host time-control commands.",
)
args = parser.parse_args()


requires(
    coherence_protocol_required=CoherenceProtocol.MESI_TWO_LEVEL,
    kvm_required=True,
)


cache_hierarchy = MESITwoLevelCacheHierarchy(
    l1d_size="16KiB",
    l1d_assoc=8,
    l1i_size="16KiB",
    l1i_assoc=8,
    l2_size="256KiB",
    l2_assoc=16,
    num_l2_banks=1,
)

memory = SingleChannelDDR3_1600(size="3GiB")

processor = SimpleSwitchableProcessor(
    starting_core_type=CPUTypes.KVM,
    switch_core_type=CPUTypes.TIMING,
    isa=ISA.X86,
    num_cores=2,
)

board = X86Board(
    clk_freq="3GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

board.set_kernel_disk_workload(
    kernel=KernelResource(
        local_path="/home/kkgiorgos/University/Diploma/Chimaera/gem5/"
        "resources/x86-linux-kernel-5.15.180"
    ),
    disk_image=DiskImageResource(
        local_path="/home/kkgiorgos/University/Diploma/Chimaera/gem5/"
        "resources/x86-ubuntu-22.04-ros-humble.img"
    ),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=/dev/sda2",
        "mce=off",
    ],
    readfile_contents="""#!/bin/bash
#!/bin/sh
echo "Hello from inside the simulated system!"
/bin/bash
""",
)

is_started = False
is_finished = False
exit_requested = False


def on_workbegin():
    global is_started

    print("[host] ROI begin reached")
    if not is_started:
        print("[host] Switching processor to detailed model")
        processor.switch()

    m5.stats.reset()
    is_started = True
    yield False


def on_workend():
    global is_finished

    print("[host] ROI end reached")
    print("[host] Dumping ROI stats")
    m5.stats.dump()

    is_finished = True
    yield True


def on_exit():
    global is_finished

    cause = simulator.get_last_exit_event_cause()
    tick = simulator.get_current_tick()
    print(f"[host] EXIT event at tick {tick}: {cause}")
    is_finished = True
    yield True


simulator = Simulator(
    board=board,
    on_exit_event={
        ExitEvent.WORKBEGIN: on_workbegin(),
        ExitEvent.WORKEND: on_workend(),
        ExitEvent.EXIT: on_exit(),
    },
)


def seconds_to_ticks(seconds):
    seconds = float(seconds)
    if seconds <= 0.0:
        raise ValueError("seconds must be positive")
    return m5.ticks.fromSeconds(seconds)


def parse_positive_ticks(value):
    ticks = int(value)
    if ticks <= 0:
        raise ValueError("ticks must be positive")
    return ticks


def base_response():
    response = {
        "ok": True,
        "status": "complete",
        "complete": True,
        "done": is_finished,
        "sim_done": is_finished,
        "sim_status": "done" if is_finished else "running",
        "tick": simulator.get_current_tick(),
        "roi_started": is_started,
    }
    if simulator._last_exit_event is not None:
        response["last_exit_cause"] = simulator.get_last_exit_event_cause()
    return response


def run_for_ticks(ticks):
    if is_finished:
        return base_response()

    start_tick = simulator.get_current_tick()
    simulator.run(max_ticks=ticks)
    response = base_response()
    response["advanced_ticks"] = simulator.get_current_tick() - start_tick
    response["requested_ticks"] = ticks
    return response


def handle_text_command(line):
    parts = line.split()
    if not parts:
        raise ValueError("empty command")

    cmd = parts[0].upper()
    if cmd == "STEP_SECONDS":
        if len(parts) != 2:
            raise ValueError(f"{cmd} requires exactly one argument")
        return run_for_ticks(seconds_to_ticks(parts[1]))

    if cmd == "STEP_TICKS":
        if len(parts) != 2:
            raise ValueError(f"{cmd} requires exactly one argument")
        return run_for_ticks(parse_positive_ticks(parts[1]))

    if cmd == "QUIT":
        global exit_requested
        exit_requested = True
        return base_response()

    raise ValueError(f"unknown command: {cmd}")


def handle_command(command):
    command = command.strip()
    return handle_text_command(command)


def serve(socket_path):
    socket_file = Path(socket_path)
    if socket_file.exists():
        socket_file.unlink()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(socket_path)
        server.listen(8)
        print(f"[host] Time-control socket listening at {socket_path}")

        while not exit_requested:
            conn, _ = server.accept()
            with conn:
                command = conn.recv(4096).decode("utf-8")
                try:
                    response = handle_command(command)
                except Exception as exc:
                    response = base_response()
                    response["ok"] = False
                    response["error"] = str(exc)

                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


# m5.ticks.fromSeconds requires the global frequency to be fixed.
m5.ticks.fixGlobalFrequency()

try:
    serve(args.socket_path)
finally:
    socket_file = Path(args.socket_path)
    if socket_file.exists():
        socket_file.unlink()

print(
    "Exiting @ tick {} because {}.".format(
        simulator.get_current_tick(),
        simulator.get_last_exit_event_cause()
        if simulator._last_exit_event is not None
        else "socket server shut down",
    )
)
