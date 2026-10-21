"""Exercise production pin handlers with synchronous interrupt acknowledgement.

Compile the real raise/lower methods in a minimal C++ device harness. Dispatch
stands in for sendAtomic's event-queue migration: another CPU can acknowledge
and lower the input before the original raise returns.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[3] / "src/dev/x86"


def method(source, qualified_name):
    start = source.index("void\n" + qualified_name + "(")
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@pytest.mark.parametrize(
    "device,dispatch",
    [("I82094AA", "requestInterrupt"), ("I8259", "signalInterrupt")],
)
def test_acknowledgement_during_dispatch_preserves_next_edge(
    tmp_path, device, dispatch
):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("requires g++")
    source = (SOURCE / (device.lower() + ".cc")).read_text()
    handlers = "\n".join(
        method(source, f"X86ISA::{device}::{action}InterruptPin")
        for action in ("raise", "lower")
    )
    probe = tmp_path / "probe.cc"
    probe.write_text(
        textwrap.dedent(
            """\
        #include <cassert>
        #include <functional>
        #include <stdexcept>
        #define DPRINTF(...) ((void)0)
        #define fatal(...) throw std::runtime_error("invalid interrupt pin")
        namespace X86ISA {
        class DEVICE {
          public:
            static constexpr int NumLines = 24;
            static constexpr int TableSize = 24;
            bool pinStates[NumLines]{};
            int delivered = 0;
            std::function<void()> onDispatch;
            void DISPATCH(int) {
                ++delivered;
                if (onDispatch) onDispatch();
            }
            void raiseInterruptPin(int);
            void lowerInterruptPin(int);
        };
        }
        HANDLERS
        int main() {
            X86ISA::DEVICE dev;
            constexpr int line = 6;
            dev.raiseInterruptPin(line);
            dev.raiseInterruptPin(line);
            assert(dev.delivered == 1);
            dev.lowerInterruptPin(line);
            dev.raiseInterruptPin(line);
            assert(dev.delivered == 2);
            dev.lowerInterruptPin(line);
            dev.onDispatch = [&] { dev.lowerInterruptPin(line); };
            dev.raiseInterruptPin(line);
            assert(dev.delivered == 3);
            // A nested ACK must survive return from interrupt delivery.
            assert(!dev.pinStates[line]);
            dev.raiseInterruptPin(line);
            assert(dev.delivered == 4);
        }
        """
        )
        .replace("DEVICE", device)
        .replace("DISPATCH", dispatch)
        .replace("HANDLERS", handlers)
    )
    executable = tmp_path / "probe"
    compiled = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(probe),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
