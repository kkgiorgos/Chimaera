# Startup failures: lost disk interrupts during KVM boot

Experiment `dc58cdec8a3f4b308f46f7ef1d5916f3` failed near startup in
`case-007` on `pop-os` (this checkout) and `case-001` on `ubuntu-george`.
The controller had already collected both attempts and removed the worker copies.
`failed-cases.json` retains the case/attempt IDs, original collection hashes,
and relevant serial/launch excerpts from those collected results.

Both guests reported `ata1: lost interrupt (Status 0x50)` and disk read errors.
The local guest could not read Python's standard library and exited through
`CHIMAERA_GUEST_INIT_FAILED=1` / `m5_fail`; the host then reported startup
cancellation and a missing timing socket during shutdown. The remote guest
stalled during filesystem startup until the host's timing-socket wait expired.
Neither timing server reached its listening phase. Neither case was OOM killed.

## Cause

`I82094AA::raiseInterruptPin()` set `pinStates[number]` after dispatching the
interrupt. Atomic interrupt delivery can migrate to a KVM CPU's event queue,
releasing the device queue. The guest can acknowledge the disk interrupt and
lower the input before the original dispatch returns. The late assignment then
overwrites the lowered state. On the next disk completion, the I/O APIC sees
an already-raised pin and suppresses the interrupt. Linux eventually times out
its ATA request, attempts recovery, and may fail the read.

`old-interrupt-trace.txt` reproduces the fault with the existing application
image and the experiment's actual local disk/kernel/module in an isolated,
four-core KVM boot using `orchestrator/kernel/smoke.py` (four cores instead of
two). It records IRQ14 delivery, acknowledgement and lowering at tick
`2200001000133`, followed by a fresh disk completion/raise at
`2202001000133` with no I/O APIC delivery. The boot subsequently logged four
lost interrupts and three disk read errors and failed the smoke check.
The full trace and boot logs remain in `/tmp/chimaera-startup-probe/old/`.

The fix records the raised state before delivery, preserving a concurrent
acknowledgement. The legacy `I8259` PIC has the same ordering defect and receives
the same correction. No timeout extension or disk/image changes are needed.

The corrected four-core Linux/DMA smoke passed with **zero lost interrupts**
and **zero disk I/O errors**, using the same guest artifacts.
`boot-comparison.json` records the before/after result;
`fixed-interrupt-trace.txt` shows the next IRQ14 being delivered after a nested
acknowledgement.

The Ramulator-enabled simulator also booted `case-007`'s original frozen
application bundle/configuration in a separate container. It switched to four
TimingSimpleCPUs, answered `STATUS` with `PAUSED`, answered `QUIT` with `BYE`,
and exited with code zero. There were no lost interrupts or disk read errors.
`full-application-startup.json` records the binary hash and CPU state;
`full-application-startup.txt` retains the relevant simulator log.

The initial probe's final serial-string assertion failed because UART output
was truncated at the workbegin pause. Its socket-response and exit-code
assertions had already passed. The post-run check instead verified the timing
server's ready/stopped tick, the saved four-core detailed CPU state, and the
absence of disk errors. The probe's readiness oracle was corrected accordingly.
No detailed steps or complete catching trial were run in this startup check.
The host-built test binary's HDF5 libraries were mounted into the disposable
container under `/probe/lib`; no running worker image was modified.

## Regression

The focused C++ harness compiles the production raise/lower methods with a
dispatch callback that acknowledges during delivery. Both original handlers
fail; both corrected handlers pass. Duplicate raises and ordinary lower/raise
cycles remain covered.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  gem5/tests/chimaera/interrupt_pin_reentry
(cd gem5 && scons build/X86/gem5.opt -j2 \
  RAMULATOR2_ROOT=ext/ramulator2/ramulator2)
git diff --check
```

The running experiment uses frozen sources and prebuilt application images.
Using this fix in orchestrated runs requires rebuilding the SDK/application
images from the corrected checkout and starting an experiment with that identity.
The existing running cases and frozen experiment inputs were left untouched.
