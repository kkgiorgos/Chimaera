# Managed DMA guest kernel validation

The orchestrator's managed recipe built Linux **5.15.180-chimaera** and
`chimaera_dma.ko` on `ubuntu-george`. The kernel and module were built together
with module versioning enabled. The source archive checksum, toolchain/compiler,
resolved-config hash, module vermagic and binary hashes are retained in
[`validation.json`](validation.json).

The real KVM boot gate used the retained automatically generated guest disk and
application image. It loaded the driver with `finit_module`, opened
`/dev/chimaera-dma`, and checked the ABI and buffer geometry with `CHDMA_IOC_INFO`.
The guest exited successfully. No `I/O error, dev sda` appeared during this check.
This does not establish the root cause of disk errors from the previous prebuilt
kernel, or qualify a full robot benchmark.

The producer then streamed the artifact bundle directly to `pop-os` using the
orchestrator's existing sparse-asset transfer helper. The consumer verified the
producer manifest and all five artifact hashes. The transfer contained the ELF
kernel, external module, resolved configuration, symbol versions and build metadata.

The full backend/container suite passed **243 tests**. The final cache-input
checks also passed independently, including rejection of source changes during
compilation. The dashboard passed its production build and TypeScript checks.

For a new experiment, select `fixed.transport_backend: "dma"` and leave
`fixed.dma_module` and the producer's kernel override empty. Finalize the previous
stopped experiment to release the queue first. Preparation builds or reuses a
matching pair and performs the boot gate before making workers ready. No new
robot benchmark was started as part of this validation.
