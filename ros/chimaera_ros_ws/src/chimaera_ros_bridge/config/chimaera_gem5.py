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

"""Importable Chimaera timing integration for experiment-owned gem5 configs.

Call configure_ticks() before constructing the board. Construct Simulator with
TimingServer.exit_handlers(), then pass it to TimingServer.run(). The optional
on_ready callback performs experiment setup once at the first workbegin marker,
before ROI statistics reset and before the timing socket becomes available.
"""
import base64
from pathlib import Path
import shlex
import signal
import socket

import m5
import m5.ticks
from gem5.simulate.exit_event import ExitEvent


def configure_ticks():
    """Use the 1 THz tick frequency required by the host timing protocol."""
    m5.ticks.setGlobalFrequency("1THz")
    m5.ticks.fixGlobalFrequency()


def add_chimaera_arguments(parser, *, guest_command="/opt/chimaera/session/guest_start"):
    """Add the transport/guest startup flags shared by experiment configs."""
    parser.add_argument("--socket-path", default="/tmp/chimaera_time.sock")
    parser.add_argument("--managed-shutdown", action="store_true",
                        help="Let the host send QUIT on launch shutdown")
    parser.add_argument("--guest-command", default=guest_command)
    parser.add_argument("--guest-file", action="append", default=[], metavar="GUEST_PATH=HOST_PATH",
                        help="Inject a file into the guest at boot; may be repeated")


def guest_start_script(command, files=(), *, preamble=""):
    """Build readfile startup content; file destinations must already have parents.

    files contains GUEST_PATH=HOST_PATH entries. preamble is trusted experiment
    shell code, run before file injection and the privileged guest command.
    """
    script = "#!/bin/bash\nset -eo pipefail\n" + preamble
    if preamble and not preamble.endswith("\n"):
        script += "\n"
    for entry in files:
        guest_path, separator, host_path = entry.partition("=")
        target = Path(guest_path)
        if not separator or not target.is_absolute() or '..' in target.parts or target == Path('/'):
            raise ValueError("guest files require an absolute GUEST_PATH=HOST_PATH")
        source = Path(host_path)
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        destination = shlex.quote(guest_path)
        mode = '755' if source.stat().st_mode & 0o111 else '644'
        script += f"printf %s {encoded} | base64 -d | sudo -n tee {destination} >/dev/null\n"
        script += f"sudo -n chmod {mode} {destination}\n"
    return script + "exec sudo -n " + shlex.quote(command) + "\n"


def parse_ticks(value, allow_zero=False):
    if not value.isascii() or not value.isdecimal():
        raise ValueError("expected a decimal integer")
    result = int(value)
    if result == 0 and not allow_zero:
        raise ValueError("duration must be positive")
    return result


class TimingServer:
    """Own the Chimaera event lifecycle and socket protocol, using a supplied simulator."""

    def __init__(self, *, on_ready=None):
        self.on_ready = on_ready
        self.roi_started = False
        self.finished = False
        self.exit_requested = False
        self.simulator = None

    def on_workbegin(self):
        while True:
            if not self.roi_started:
                if self.on_ready:
                    self.on_ready()
                self.roi_started = True
                m5.stats.reset()
            yield True

    def on_workend(self):
        while True:
            m5.stats.dump()
            self.finished = True
            yield True

    def on_exit(self):
        while True:
            # Guest boot exits are boundaries, not end-of-workload.
            yield True

    def on_max_tick(self):
        while True:
            yield True

    def exit_handlers(self):
        """Pass this mapping as Simulator(on_exit_event=...)."""
        return {ExitEvent.WORKBEGIN: self.on_workbegin(),
                ExitEvent.WORKEND: self.on_workend(),
                ExitEvent.EXIT: self.on_exit(),
                ExitEvent.MAX_TICK: self.on_max_tick()}

    def _run_once(self):
        self.simulator.run()
        cause = self.simulator.get_last_exit_event_cause()
        # Recent gem5 guest images finish after_boot.sh with hypercall 3.
        # Simulator handles it, but the legacy cause translator does not know
        # hypercalls. This is a boot boundary, just like the legacy m5 exit.
        if cause == "m5_hypercall instruction encountered" and self.simulator.get_hypercall_id() == 3:
            event = ExitEvent.EXIT
        else:
            event = ExitEvent.translate_exit_status(cause)
        if event not in (ExitEvent.EXIT, ExitEvent.WORKBEGIN, ExitEvent.WORKEND, ExitEvent.MAX_TICK):
            self.finished = True
        return event

    def run_for_ticks(self, ticks):
        if self.finished:
            return f"DONE {m5.curTick()}"
        start = m5.curTick()
        target = start + ticks
        if ticks > 3_600_000_000_000_000 or target >= m5.MaxTick:
            raise ValueError("step exceeds one hour or the gem5 tick range")
        while m5.curTick() < target and not self.finished:
            # m5.simulate uses a relative budget. Handling an intermediate event
            # must not grant a fresh full interval (the old config did that).
            self.simulator.set_max_ticks(target - m5.curTick())
            event = self._run_once()
            if event == ExitEvent.MAX_TICK:
                break  # Report actual progress; the host corrects any drift later.
        end = m5.curTick()
        if self.finished:
            return f"DONE {end}"
        return f"OK {start} {end}"

    def handle_command(self, line):
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("STEP_US", "STEP_NS", "STEP_TICKS"):
            value = parse_ticks(parts[1], allow_zero=parts[0] == "STEP_TICKS")
            scale = {"STEP_US": 1000000, "STEP_NS": 1000, "STEP_TICKS": 1}[parts[0]]
            return self.run_for_ticks(value * scale)
        if parts == ["STATUS"]:
            return f"{'DONE' if self.finished else 'PAUSED'} {m5.curTick()}"
        if parts == ["QUIT"]:
            if self.roi_started:
                m5.stats.dump()
            self.exit_requested = True
            return "BYE"
        raise ValueError("expected STEP_US n, STEP_NS n, STEP_TICKS n, STATUS, or QUIT")

    def serve(self, path, *, managed_shutdown=False):
        socket_file = Path(path)
        identity = None
        previous_sigint = None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                # Never unlink an existing path: another simulator may own it.
                server.bind(path)
                identity = socket_file.lstat()
                server.listen(8)
                if managed_shutdown:
                    # ROS launch signals all children together; keep the timing server
                    # available for the host's QUIT. SIGTERM remains the fallback.
                    previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
                print(f"[host] Timing socket ready at {path}; gem5 paused at {m5.curTick()}", flush=True)
                while not self.exit_requested:
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
                            response = self.handle_command(data.decode("ascii").strip())
                        except Exception as error:
                            response = "ERROR " + str(error).replace("\n", " ")
                        try:
                            conn.settimeout(10)
                            conn.sendall((response + "\n").encode("ascii", errors="replace"))
                        except OSError:
                            # The step is still complete and the simulator stays paused.
                            pass
        finally:
            if previous_sigint is not None:
                signal.signal(signal.SIGINT, previous_sigint)
            if identity is not None:
                try:
                    current = socket_file.lstat()
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        socket_file.unlink()
                except FileNotFoundError:
                    pass

    def run(self, simulator, socket_path="/tmp/chimaera_time.sock", *, managed_shutdown=False):
        """Boot to workbegin, then serve host-controlled steps without wall-time pacing."""
        self.simulator = simulator
        print("[host] Booting until the guest bridge's workbegin marker", flush=True)
        while not self.roi_started and not self.finished:
            self._run_once()
        if self.finished:
            raise RuntimeError("guest finished before its bridge was ready")
        self.serve(socket_path, managed_shutdown=managed_shutdown)
        print(f"[host] Timing server stopped at tick {m5.curTick()}", flush=True)
