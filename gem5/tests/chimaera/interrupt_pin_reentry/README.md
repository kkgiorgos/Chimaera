# Interrupt acknowledgement during delivery

Run from the repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  gem5/tests/chimaera/interrupt_pin_reentry
```

Requires pytest and g++. The test compiles the production I/O APIC and PIC
raise/lower handlers in a small C++ harness, replacing only interrupt dispatch
with a callback. It covers duplicate raises, ordinary lower/raise cycles, and
an acknowledgement that lowers the pin before dispatch returns. That last
ordering occurs when atomic delivery releases the device event queue for KVM.
The old handlers fail because they overwrite the acknowledgement and suppress
the next edge. This deterministic test does not boot Linux or exercise the
full interrupt routing graph; full-system evidence is recorded separately in
`docs/measurements/startup-interrupt-2026-10-21/` at the repository root.
