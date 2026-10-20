/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_DMA_H
#define GEM5_CHIMAERA_DMA_H

#include <linux/types.h>
#include <linux/ioctl.h>

/* Little-endian x86 PCI ABI shared by the model, kernel and user transport. */
#define CHDMA_VENDOR_ID 0x1af4
#define CHDMA_DEVICE_ID 0xc101
#define CHDMA_ABI_VERSION 1
#define CHDMA_MAGIC 0x4348444d
#define CHDMA_PAGE_BYTES 4096
#define CHDMA_MAX_BYTES (32U * 1024U * 1024U)
#define CHDMA_MAX_SEGMENTS (CHDMA_MAX_BYTES / CHDMA_PAGE_BYTES)

#define CHDMA_REG_MAGIC 0x00
#define CHDMA_REG_VERSION 0x04
#define CHDMA_REG_STATUS 0x08
#define CHDMA_REG_ACK 0x0c
#define CHDMA_REG_RX_POST 0x10
#define CHDMA_REG_TX_POST 0x18
#define CHDMA_REG_RX_COOKIE 0x20
#define CHDMA_REG_TX_COOKIE 0x28
#define CHDMA_REG_RX_LENGTH 0x30
#define CHDMA_REG_ERROR 0x34
#define CHDMA_REG_RESET 0x38

#define CHDMA_IRQ_RX 0x01
#define CHDMA_IRQ_TX 0x02
#define CHDMA_IRQ_ERROR 0x04
#define CHDMA_IRQ_RESET 0x08
#define CHDMA_STATUS_TX_BUSY 0x100
#define CHDMA_STATUS_RX_BUSY 0x200
#define CHDMA_STATUS_RESETTING 0x400

#define CHDMA_ERROR_NONE 0
#define CHDMA_ERROR_DESCRIPTOR 1
#define CHDMA_ERROR_QUEUE_FULL 2
#define CHDMA_ERROR_BUSY 3
#define CHDMA_ERROR_MAILBOX 4

struct chdma_descriptor {
    __le32 length;
    __le32 segments;
    __le64 cookie;
};
struct chdma_segment {
    __le64 address;
    __le32 length;
    __le32 reserved;
};
struct chdma_info {
    __u32 abi_version;
    __u32 rx_slots;
    __u32 tx_slots;
    __u32 slot_bytes;
    __u32 page_bytes;
    __u32 reserved;
};
struct chdma_submit {
    __u32 slot;
    __u32 length;
};
struct chdma_completion {
    __u32 events;
    __u32 slot;
    __u32 length;
    __u32 error;
};
#define CHDMA_IOC_INFO _IOR('C', 0x10, struct chdma_info)
#define CHDMA_IOC_TX _IOW('C', 0x11, struct chdma_submit)
#define CHDMA_IOC_RX _IOW('C', 0x12, struct chdma_submit)
#define CHDMA_IOC_RESET _IO('C', 0x13)

#endif
