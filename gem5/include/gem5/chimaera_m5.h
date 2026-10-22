/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_M5_H
#define GEM5_CHIMAERA_M5_H
#include <linux/types.h>
#include <linux/ioctl.h>

/* Little endian x86-64 ABI. Payloads use coherent functional accesses, no DMA. */
#define CHM5_VENDOR_ID 0x1af4
#define CHM5_DEVICE_ID 0xc102
#define CHM5_MAGIC 0x43484d35
#define CHM5_ABI_VERSION 1
#define CHM5_MAX_CONTEXTS 64
#define CHM5_MAX_SLOTS 16
#define CHM5_MAX_CHANNELS 64
#define CHM5_MAX_BYTES (32U * 1024U * 1024U)
#define CHM5_PAGE_BYTES 4096U
#define CHM5_CONTROL 0
#define CHM5_BULK 1
#define CHM5_BULK_PORT_BIT 0x40000000U
#define CHM5_APPLICATION_PORT_BIT 0x20000000U
#define CHM5_FEATURE_FUNCTIONAL 1U
#define CHM5_FEATURE_PHYSICAL_RX 2U
#define CHM5_FEATURE_LANES 4U
#define CHM5_FEATURE_ADMISSION_IRQ 8U
#define CHM5_FEATURE_RX_BATCH 16U

#define CHM5_REG_MAGIC 0x00
#define CHM5_REG_VERSION 0x04
#define CHM5_REG_IRQ 0x08 /* u64 sticky context mask */
#define CHM5_REG_REGISTER 0x10 /* u64 physical chm5_registration address */
#define CHM5_REG_ACK_IRQ 0x18 /* u64 context mask */
#define CHM5_REG_ADMISSION_EPOCH 0x20 /* u64 completed host admission count */
#define CHM5_REG_FEATURES 0x28 /* u32 feature mask */
#define CHM5_CONTEXT_BASE 0x100
#define CHM5_CONTEXT_STRIDE 0x40
#define CHM5_CTX_GENERATION 0x00 /* u64 bind generation, zero if unregistered */
#define CHM5_CTX_SEQUENCE 0x08 /* u64 persistent notification sequence */
#define CHM5_CTX_STATUS 0x10 /* u32 level readiness */
#define CHM5_CTX_ERROR 0x14 /* u32 positive errno */
#define CHM5_CTX_CLOSE 0x18 /* u32 synchronous unregister, drops all references */
#define CHM5_CTX_ARM_ADMISSION 0x1c /* u32 first-ingress notification enable */
#define CHM5_STATUS_RX 1U
#define CHM5_STATUS_ERROR 2U
#define CHM5_STATUS_SHUTDOWN 4U
#define CHM5_STATUS_ADMISSION 8U

/* Kernel publishes this header followed by slots * pages_per_slot u64 physical
 * page addresses. Pages must be aligned, pinned WB RAM, zero offset per slot.
 * REGISTER is synchronous and copies the page table; the table can then free.
 * CLOSE is synchronous, invalidates generation and drops all physical refs
 * before kernel memory may free. Existing userspace loans retain the file/VMA. */
/* size is sizeof(struct chm5_registration), excluding the physical-page tail. */
struct chm5_registration {
    __u32 version, size;
    __u32 context, process;
    __u32 lane, slots;
    __u32 slot_bytes, pages_per_slot;
};
struct chm5_bind {
    __u32 version, lane;
    __u32 context, process;
    __u32 slots, slot_bytes;
    __u64 generation;
};
#define CHM5_IOC_BIND _IOWR('M', 0x20, struct chm5_bind)
/* Bound context only. Sleeps interruptibly until the first host ingress,
 * returns its nonzero epoch; no userspace polling or payload participation. */
#define CHM5_IOC_WAIT_ADMISSION _IOR('M', 0x21, __u64)
/* Poll only: level status from the device is authoritative; IRQs just wake
 * waiters. File release synchronously unregisters before freeing RX pages. */

#define CHM5_CTL_QUERY 0
#define CHM5_CTL_BIND 1
#define CHM5_CTL_OPEN_CHANNEL 2
#define CHM5_CTL_CLOSE_CHANNEL 3
#define CHM5_CTL_ARM 4
#define CHM5_CTL_CLOSE 5
#define CHM5_CTL_REGISTER 6 /* kernel-only physical registration via PCI */
#define CHM5_CTL_UNREGISTER 7 /* kernel-only physical unregister via PCI */
#define CHM5_DATA_SUBMITV 1
#define CHM5_DATA_REAP 2
#define CHM5_DATA_RELEASE 3
#define CHM5_DATA_POST_RX 4 /* initial posting is atomic with kernel REGISTER */
#define CHM5_DATA_REAP_BATCH 5
#define CHM5_DATA_RELEASE_BATCH 6

/* Batch commands use address0/bytes0 for up to MAX_SLOTS completions.
 * REAP_BATCH writes the currently ready prefix and request.length=count, or
 * EAGAIN if empty. It never waits to fill a batch. RELEASE_BATCH validates all
 * slot/packet identities before returning any credit and writes no reply. */
struct chm5_completion {
    __u32 slot, length;
    __u64 packet_sequence, admission_epoch;
};

/* Both m5 entrypoints take (command, request_virtual_address, sizeof(request)).
 * Return 0 on success or negative Linux errno encoded in uint64_t. Every call
 * validates version/size/context/generation. QUERY needs only version/size.
 * SUBMITV copies both iovecs before success; EAGAIN accepts nothing.
 * REAP returns EAGAIN for empty; successful slot remains loaned until RELEASE.
 * BIND confirms kernel context ownership. ARM returns current sequence/status,
 * and never clears readiness of queued records. No waiting inside handlers. */
struct chm5_request {
    __u32 version, size;
    __u32 context, lane;
    __u64 generation;
    __u64 sequence;
    __u32 process, port;
    __u32 slot, length;
    __u32 status, error;
    __u64 address0, bytes0;
    __u64 address1, bytes1;
    __u64 admission_epoch;
    __u64 packet_sequence;
};
#ifdef __cplusplus
static_assert(sizeof(chm5_registration) == 32, "Chimaera m5 registration ABI");
static_assert(sizeof(chm5_bind) == 32, "Chimaera m5 bind ABI");
static_assert(sizeof(chm5_request) == 104, "Chimaera m5 request ABI");
static_assert(sizeof(chm5_completion) == 24, "Chimaera m5 completion ABI");
#endif
#endif
