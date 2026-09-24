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

"""Socket-controlled gem5 for chimaera::Gem5TimingController.

One ASCII line per connection:
  STEP_US <positive integer>  -> OK <start_tick> <end_tick>
  STEP_NS <positive integer>  -> OK <start_tick> <end_tick>
  STEP_TICKS <nonnegative integer> -> OK <actual_start_tick> <actual_end_tick>
  STATUS -> PAUSED <tick> or DONE <tick>
  QUIT -> BYE (terminates gem5 without resuming the guest)
Errors are ERROR <description>. Early simulation termination is DONE <tick>.
The tick frequency is fixed at 1 THz: one nanosecond is 1000 ticks.

By default the simulator starts paused at tick zero. --boot-to-controller
boots the deployed gem5_controller_guest and pauses at its workbegin marker
before opening the timing socket. Host data listeners must be started first.
The guest stays on KVM throughout; workbegin does not switch CPU models.
"""

import argparse
import socket
from pathlib import Path

import m5
import m5.ticks
from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--socket-path", default="/tmp/chimaera_time.sock")
parser.add_argument("--boot-to-controller", action="store_true")
args = parser.parse_args()
m5.ticks.setGlobalFrequency("1THz")
m5.ticks.fixGlobalFrequency()

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

processor = SimpleProcessor(
    cpu_type=CPUTypes.KVM,
    isa=ISA.X86,
    num_cores=2,
)

for core in processor.get_cores():
    core.get_simobject().usePerf = False

board = X86Board(
    clk_freq="3GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

resources = Path(__file__).resolve().parents[2] / "resources"
board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=str(resources / "x86-linux-kernel-5.15.180")),
    disk_image=DiskImageResource(local_path=str(resources / "x86-ubuntu-22.04-ros-humble.img")),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=/dev/sda2",
        "mce=off",
    ],
    readfile_contents=(
        "#!/bin/bash\nexec sudo /usr/local/bin/gem5_controller_guest\n"
        if args.boot_to_controller else "#!/bin/bash\n/bin/bash\n"
    ),
)

roi_started = False
finished = False
exit_requested = False


def on_workbegin():
    global roi_started
    while True:
        roi_started = True
        m5.stats.reset()
        yield True  # Return to Python to recompute the remaining step budget.


def on_workend():
    global finished
    while True:
        m5.stats.dump()
        finished = True
        yield True


def on_exit():
    while True:
        # The image emits boot exits. They are boundaries, not end-of-workload.
        yield True


def on_max_tick():
    while True:
        yield True


simulator = Simulator(board=board, on_exit_event={
    ExitEvent.WORKBEGIN: on_workbegin(),
    ExitEvent.WORKEND: on_workend(),
    ExitEvent.EXIT: on_exit(),
    ExitEvent.MAX_TICK: on_max_tick(),
})


def parse_ticks(value, allow_zero=False):
    if not value.isascii() or not value.isdecimal():
        raise ValueError("expected a decimal integer")
    result = int(value)
    if result == 0 and not allow_zero:
        raise ValueError("duration must be positive")
    return result


def run_for_ticks(ticks):
    global finished
    if finished:
        return f"DONE {m5.curTick()}"
    start = m5.curTick()
    target = start + ticks
    if ticks > 3_600_000_000_000_000 or target >= m5.MaxTick:
        raise ValueError("step exceeds one hour or the gem5 tick range")
    while m5.curTick() < target and not finished:
        # m5.simulate uses a relative budget. Handling an intermediate event
        # must not grant a fresh full interval (the old config did that).
        simulator.set_max_ticks(target - m5.curTick())
        simulator.run()
        event = ExitEvent.translate_exit_status(simulator.get_last_exit_event_cause())
        if event == ExitEvent.MAX_TICK:
            break  # Report actual progress; the host corrects any drift later.
        if event not in (ExitEvent.EXIT, ExitEvent.WORKBEGIN, ExitEvent.WORKEND, ExitEvent.MAX_TICK):
            finished = True
    end = m5.curTick()
    if finished:
        return f"DONE {end}"
    return f"OK {start} {end}"


def handle_command(line):
    global exit_requested
    parts = line.split()
    if len(parts) == 2 and parts[0] in ("STEP_US", "STEP_NS", "STEP_TICKS"):
        value = parse_ticks(parts[1], allow_zero=parts[0] == "STEP_TICKS")
        scale = {"STEP_US": 1000000, "STEP_NS": 1000, "STEP_TICKS": 1}[parts[0]]
        return run_for_ticks(value * scale)
    if parts == ["STATUS"]:
        return f"{'DONE' if finished else 'PAUSED'} {m5.curTick()}"
    if parts == ["QUIT"]:
        exit_requested = True
        return "BYE"
    raise ValueError("expected STEP_US n, STEP_NS n, STEP_TICKS n, STATUS, or QUIT")


def serve(path):
    socket_file = Path(path)
    identity = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            # Never unlink an existing path: another simulator may own it.
            server.bind(path)
            identity = socket_file.lstat()
            server.listen(8)
            print(f"[host] Timing socket ready at {path}; gem5 paused at {m5.curTick()}", flush=True)
            while not exit_requested:
                conn, _ = server.accept()
                with conn:
                    try:
                        conn.settimeout(10)  # Bound incomplete command reads.
                        data = bytearray()
                        while not data.endswith(b"\n"):
                            chunk = conn.recv(1)
                            if not chunk:
                                raise ValueError("connection closed before newline")
                            data.extend(chunk)
                            if len(data) > 4096:
                                raise ValueError("command exceeds 4096 bytes")
                        conn.settimeout(None)  # Simulation may take arbitrary wall time.
                        response = handle_command(data.decode("ascii").strip())
                    except Exception as error:
                        response = "ERROR " + str(error).replace("\n", " ")
                    try:
                        conn.settimeout(10)
                        conn.sendall((response + "\n").encode("ascii", errors="replace"))
                    except OSError:
                        # The step is still complete and the simulator stays paused.
                        pass
    finally:
        if identity is not None:
            try:
                current = socket_file.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    socket_file.unlink()
            except FileNotFoundError:
                pass


if args.boot_to_controller:
    print("[host] Booting until the controller's workbegin marker", flush=True)
    while not roi_started and not finished:
        simulator.run()
    if finished:
        raise RuntimeError("guest finished before its controller was ready")

serve(args.socket_path)
print(f"[host] Timing server stopped at tick {m5.curTick()}")
