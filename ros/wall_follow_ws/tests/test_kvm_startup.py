"""Exercise gem5's actual interrupt-handling methods with queued INIT/SIPI.

The small C++ harness supplies scheduling and KVM-call doubles. It does not
boot an image, use /dev/kvm, or run a gem5 simulation.
"""
from pathlib import Path
import shutil
import subprocess

import pytest


def method(source, name):
    start = source.index('void\nX86KvmCPU::' + name + '()')
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def test_queued_startup_interrupt_wakes_after_init(tmp_path):
    compiler = shutil.which('g++')
    if not compiler:
        pytest.skip('g++ is needed for the C++ interrupt regression harness')
    root = Path(__file__).resolve().parents[3]
    source = (root/'gem5/src/arch/x86/kvm/x86_cpu.cc').read_text()
    harness = r'''
#include <cassert>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>
#define DPRINTF(...) ((void)0)
#define panic(...) throw std::runtime_error("unexpected interrupt")
using Cycles = unsigned;
struct Thread {
    bool active = true;
    bool started = false;
    void suspend() { active = false; }
};
struct FaultBase {
    virtual ~FaultBase() = default;
    virtual void invoke(Thread *) {}
    virtual const char *name() { return "fault"; }
};
struct X86Interrupt : FaultBase { unsigned getVector() { return 0; } };
struct InitInterrupt : X86Interrupt {};
struct StartupInterrupt : X86Interrupt {
    void invoke(Thread *thread) override { thread->started = true; }
};
struct NonMaskableInterrupt : X86Interrupt {};
using Fault = std::shared_ptr<FaultBase>;
struct Apic {
    bool init = false;
    bool startup = false;
    int eventQueue() { return 0; }
    bool hasPendingUnmaskable() { return init || startup; }
    Fault getInterrupt() {
        if (init) return std::make_shared<InitInterrupt>();
        assert(startup);
        return std::make_shared<StartupInterrupt>();
    }
    void updateIntrInfo() { if (init) init = false; else startup = false; }
};
struct EventQueue { struct ScopedMigration { explicit ScopedMigration(int) {} }; };
namespace X86ISA { using Interrupts = Apic; }
struct Event {
    bool queued = false;
    bool scheduled() { return queued; }
};
struct kvm_interrupt { unsigned irq; };
class X86KvmCPU {
  public:
    Thread state;
    Thread *thread = &state;
    Thread *tc = &state;
    Apic apic;
    std::vector<Apic *> interrupts{&apic};
    Event pendingInterruptEvent;
    bool threadContextDirty = false;
    bool kvmUpdated = false;
    unsigned wakeups = 0;
    unsigned scheduledFor = 0;
    void syncThreadContext() {}
    void updateKvmState() { kvmUpdated = true; }
    void kvmNonMaskableInterrupt() {}
    void kvmInterrupt(kvm_interrupt &) {}
    void wakeup(unsigned) { ++wakeups; thread->active = true; }
    unsigned clockEdge(Cycles cycles) { return cycles; }
    void schedule(Event &event, unsigned when) {
        assert(!event.scheduled());
        assert(!thread->active); // Never reactivate within the INIT CPU tick.
        event.queued = true;
        scheduledFor = when;
    }
    void runDeferredWakeup() {
        assert(pendingInterruptEvent.scheduled());
        pendingInterruptEvent.queued = false;
        wakeupIfInterruptPending();
    }
    void wakeupIfInterruptPending();
    void deliverInterrupts();
};
'''
    harness += '\n' + method(source, 'wakeupIfInterruptPending')
    harness += '\n' + method(source, 'deliverInterrupts')
    harness += r'''
int main() {
    // Both IPIs arrived while the core was active. The original SIPI wakeup
    // cannot help after the subsequent INIT delivery suspends that core.
    X86KvmCPU queued;
    queued.apic.init = queued.apic.startup = true;
    queued.deliverInterrupts();
    assert(!queued.state.active && queued.apic.startup);
    assert(queued.threadContextDirty && queued.wakeups == 0);
    assert(queued.scheduledFor == 1);
    queued.runDeferredWakeup();
    assert(queued.state.active && queued.wakeups == 1);
    queued.deliverInterrupts();
    assert(queued.state.started && queued.kvmUpdated);
    assert(!queued.apic.hasPendingUnmaskable());

    // An INIT without a queued SIPI must leave the core suspended.
    X86KvmCPU waiting;
    waiting.apic.init = true;
    waiting.deliverInterrupts();
    waiting.runDeferredWakeup();
    assert(!waiting.state.active && waiting.wakeups == 0);

    // Cover a SIPI arriving between INIT delivery and the deferred recheck.
    X86KvmCPU arriving;
    arriving.apic.init = true;
    arriving.deliverInterrupts();
    arriving.apic.startup = true;
    arriving.runDeferredWakeup();
    arriving.deliverInterrupts();
    assert(arriving.state.active && arriving.state.started);

    // A normal startup interrupt does not enqueue an extra wakeup.
    X86KvmCPU normal;
    normal.apic.startup = true;
    normal.deliverInterrupts();
    assert(normal.state.started && normal.kvmUpdated);
    assert(!normal.pendingInterruptEvent.scheduled());
}
'''
    path = tmp_path/'kvm_startup.cc'
    binary = tmp_path/'kvm_startup'
    path.write_text(harness)
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    str(path), '-o', str(binary)], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
