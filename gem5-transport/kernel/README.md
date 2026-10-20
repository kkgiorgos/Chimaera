# Experimental coherent DMA driver

This driver and `gem5/src/chimaera/ChimaeraDma.py` implement transport ABI 1.
They are a device prototype, not the default ROS bridge backend. The current
mapping ABI targets x86-64 Linux with 4 KiB pages and coherent PCI DMA.

Build against **the running guest kernel's exact headers**, not the host kernel:

```bash
make -C /path/to/linux-headers-5.15.0-142-generic \
  M="$PWD/gem5-transport/kernel" modules -j2
modinfo gem5-transport/kernel/chimaera_dma.ko
```

The deployed guest artifact uses `5.15.0-142-generic`. A driver built for
another release must not be injected into that image. The probe loads the module
with `finit_module`, so the minimal guest does not need the `insmod` executable.
Loading requires guest root; `/dev/chimaera-dma` is deliberately mode 0600.

An exclusive open allocates four receive and eight transmit buffers, each 8 MiB
by default. Read-only module parameters `buffer_mb`, `receive_slots` and
`transmit_slots` adjust the pool; the limits are 32 MiB per buffer and 64 total
slots. Each buffer consists of order-zero coherent DMA pages and a scatter list.
This avoids a multi-megabyte physically contiguous allocation. Buffer allocation
and mapping belong before the measured ROI.

`CHDMA_IOC_INFO` reports geometry. Map one complete buffer with a shared mapping
whose file offset is `slot * page_bytes`. `CHDMA_IOC_RX` posts a receive buffer;
`CHDMA_IOC_TX` submits a filled transmit buffer. Nonblocking `read` returns one
completion and `poll` waits for interrupts. The caller must retain a receive
buffer until publication finishes, then post it again. It must retain a transmit
buffer until its TX completion. `EAGAIN` on TX is backpressure, not permission to
overwrite another slot. Error completions fail the session.

Reset waits until all outstanding descriptor and payload DMA responses finish.
Only its completion permits freeing pages. A reset timeout clears bus mastering
and deliberately retains buffers until VM teardown. Final file release resets
before freeing; VMAs retain the file while mapped. Checkpoint/restore is rejected
by the model. Reused-VM sessions and hot removal need further validation.
Reset cancels queued transfers and discards receive/completion queues; it is a
teardown or recovery operation.

The prototype uses PCI INTA on GSI 17. The validation config selects `acpi=off`
because gem5's minimal ACPI description has no PCI routing table; it supplies
an explicit MP-table entry instead. Production ACPI integration remains work.

For validation and same-machine native references, see
[`profiling/chimaera_rewrite`](../../profiling/chimaera_rewrite/README.md).
