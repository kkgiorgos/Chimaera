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

The simulator boots the deployed chimaera_wall_follow_guest
and pauses at its workbegin marker
before opening the timing socket. Host data listeners must be started first.
KVM boots to the address-based workbegin marker, then switches to a simulated
CPU (TimingSimpleCPU by default). The ROI uses instruction transport m5ops.
"""

import argparse
import base64
import re
import socket
import signal
from pathlib import Path

import m5
import m5.ticks
from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--socket-path", default="/tmp/chimaera_time.sock")
parser.add_argument("--boot-to-controller", action="store_true", default=True)
parser.add_argument("--gem5-root", required=True)
parser.add_argument("--cpu-type", choices=("timing", "o3"), default="timing")
parser.add_argument("--cpu-clock", default="3GHz")
parser.add_argument("--num-cores", type=int, default=2)
parser.add_argument("--l1d-size", default="16KiB")
parser.add_argument("--l1i-size", default="16KiB")
parser.add_argument("--l2-size", default="256KiB")
parser.add_argument("--l1-assoc", type=int, default=8)
parser.add_argument("--l2-assoc", type=int, default=16)
parser.add_argument("--image")
parser.add_argument("--kernel")
parser.add_argument("--root-device", default="/dev/sda2")
parser.add_argument("--managed-shutdown", action="store_true", help="Let the host send QUIT on launch shutdown")
parser.add_argument("--controller-file", help="Inject controller YAML into the guest at boot")
args = parser.parse_args()
if args.num_cores < 1 or args.l1_assoc < 1 or args.l2_assoc < 1:
    parser.error("core count and cache associativity must be positive")
if not re.fullmatch(r"(?:/dev/[A-Za-z0-9]+|PARTUUID=[0-9a-fA-F]{8}-[0-9a-fA-F]{2})", args.root_device):
    parser.error("invalid guest root device")
boot_script = "#!/bin/bash\nset -e\n"
if args.controller_file:
    encoded = base64.b64encode(Path(args.controller_file).read_bytes()).decode("ascii")
    boot_script += (f"printf %s {encoded} | base64 -d > /tmp/chimaera-controller.yaml\n"
                    "export CHIMAERA_ROBOT_CONFIG=/tmp/chimaera-controller.yaml\n")
boot_script += ("if (( EUID == 0 )); then\n"
                "  exec /usr/local/bin/chimaera_wall_follow_guest\n"
                "fi\n"
                "exec sudo -n env CHIMAERA_ROBOT_CONFIG=\"${CHIMAERA_ROBOT_CONFIG:-/usr/local/share/chimaera/controller.yaml}\" "
                "/usr/local/bin/chimaera_wall_follow_guest\n")
m5.ticks.setGlobalFrequency("1THz")
m5.ticks.fixGlobalFrequency()

requires(
    coherence_protocol_required=CoherenceProtocol.MESI_TWO_LEVEL,
    kvm_required=True,
)


cache_hierarchy = MESITwoLevelCacheHierarchy(
    l1d_size=args.l1d_size,
    l1d_assoc=args.l1_assoc,
    l1i_size=args.l1i_size,
    l1i_assoc=args.l1_assoc,
    l2_size=args.l2_size,
    l2_assoc=args.l2_assoc,
    num_l2_banks=1,
)

memory = SingleChannelDDR3_1600(size="3GiB")

processor = SimpleSwitchableProcessor(
    starting_core_type=CPUTypes.KVM,
    switch_core_type={"timing": CPUTypes.TIMING, "o3": CPUTypes.O3}[args.cpu_type],
    isa=ISA.X86,
    num_cores=args.num_cores,
)

for core in processor.get_cores():
    core.get_simobject().usePerf = False

board = X86Board(
    clk_freq=args.cpu_clock,
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

resources = Path(args.gem5_root).resolve() / "resources"
board.set_kernel_disk_workload(
    kernel=KernelResource(local_path=str(Path(args.kernel) if args.kernel else resources / "x86-linux-kernel-5.15.180")),
    disk_image=DiskImageResource(local_path=str(Path(args.image) if args.image else resources / "x86-ubuntu-22.04-ros-humble.img"), root_partition="2"),
    kernel_args=[
        "earlyprintk=ttyS0",
        "console=ttyS0",
        "lpj=7999923",
        "root=" + args.root_device,
        "mce=off",
    ],
    readfile_contents=(
        boot_script
        if args.boot_to_controller else "#!/bin/bash\n/bin/bash\n"
    ),
)

roi_started = False
finished = False
exit_requested = False


def on_workbegin():
    global roi_started
    while True:
        if not roi_started:
            processor.switch()
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


def on_fail():
    raise RuntimeError("guest reported a startup or workload failure; see its serial log")
    yield True


def on_max_tick():
    while True:
        yield True


simulator = Simulator(board=board, on_exit_event={
    ExitEvent.WORKBEGIN: on_workbegin(),
    ExitEvent.WORKEND: on_workend(),
    ExitEvent.EXIT: on_exit(),
    ExitEvent.FAIL: on_fail(),
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
        if roi_started:
            m5.stats.dump()
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
            if args.managed_shutdown:
                # ROS launch signals all children together; keep the timing server
                # available for the host's QUIT. SIGTERM remains the fallback.
                signal.signal(signal.SIGINT, signal.SIG_IGN)
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
