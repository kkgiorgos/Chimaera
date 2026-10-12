# x86 m5 write ordering regression

Run with a built X86 gem5 binary and GCC, while no Chimaera session is active:

```bash
python3 gem5/tests/chimaera/m5_write_ordering/run.py \
  --output /tmp/chimaera-m5-write-ordering
```

Eight small SE probes check `m5_chimaera_recv` and `m5_read_file` on Timing and
O3 CPUs, with classic caches and Ruby MESI Two Level. Each simulates less than
one microsecond. There is no Linux boot, ROS application or Gazebo simulation.
The receive probes temporarily own the fixed `/tmp/chimaera_h2g.sock` path;
they refuse existing socket entries. Logs, configuration, statistics and a
machine-readable summary are kept in the output directory.

An older load warms a zero-filled header buffer. The m5 operation writes a
big-endian header whose value is three, and an independent younger load checks
the result. Non-speculative retirement alone allows O3 to execute that younger
load early and retain zero in a physical register, despite the correct
functional write to the cache. The probe then exits with status one. Serializing
younger instructions until the m5 operation retires makes the load observe
three and exit with status zero. This exercises the actual ISA, processor and
memory paths; it does not emulate the ordering fix in Python.
