// SPDX-License-Identifier: GPL-2.0
#include <linux/dma-mapping.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/kfifo.h>
#include <linux/kref.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/poll.h>
#include <linux/uaccess.h>
#include <gem5/chimaera_dma.h>

#ifndef CONFIG_X86_64
#error "The current Chimaera DMA mapping ABI targets coherent x86-64 PCI"
#endif

static unsigned int buffer_mb = 8;
module_param(buffer_mb, uint, 0444);
static unsigned int receive_slots = 4;
module_param(receive_slots, uint, 0444);
static unsigned int transmit_slots = 8;
module_param(transmit_slots, uint, 0444);

enum buffer_state { BUFFER_FREE, BUFFER_RX, BUFFER_TX, BUFFER_READY, BUFFER_USER };
struct chdma_page { void *cpu; dma_addr_t address; };
struct chdma_buffer {
    struct chdma_page *pages;
    struct chdma_descriptor *descriptor;
    dma_addr_t descriptor_address;
    enum buffer_state state;
};
struct chdma_device {
    struct pci_dev *pci;
    void __iomem *registers;
    struct miscdevice misc;
    struct mutex mutex;
    spinlock_t lock;
    wait_queue_head_t wait;
    struct kref references;
    DECLARE_KFIFO(completions, struct chdma_completion, 64);
    struct chdma_buffer *buffers;
    unsigned int slots, rx_slots, bytes, pages;
    size_t descriptor_bytes;
    bool opened, removed, faulted, reset_done, safe_to_free;
};

static void free_buffers(struct chdma_device *device)
{
    unsigned int slot, page;
    if (!device->buffers || !device->safe_to_free) return;
    for (slot = 0; slot < device->slots; ++slot) {
        struct chdma_buffer *buffer = &device->buffers[slot];
        if (buffer->pages) {
            for (page = 0; page < device->pages; ++page)
                if (buffer->pages[page].cpu)
                    dma_free_coherent(&device->pci->dev, PAGE_SIZE,
                        buffer->pages[page].cpu, buffer->pages[page].address);
            kfree(buffer->pages);
        }
        if (buffer->descriptor)
            dma_free_coherent(&device->pci->dev, device->descriptor_bytes,
                buffer->descriptor, buffer->descriptor_address);
    }
    kfree(device->buffers);
    device->buffers = NULL;
}

static void release_device(struct kref *reference)
{
    struct chdma_device *device = container_of(reference, struct chdma_device, references);
    free_buffers(device);
    pci_dev_put(device->pci);
    kfree(device);
}

static int allocate_buffers(struct chdma_device *device)
{
    unsigned int slot, page;
    device->safe_to_free = true;
    device->buffers = kcalloc(device->slots, sizeof(*device->buffers), GFP_KERNEL);
    if (!device->buffers) return -ENOMEM;
    for (slot = 0; slot < device->slots; ++slot) {
        struct chdma_buffer *buffer = &device->buffers[slot];
        struct chdma_segment *segments;
        buffer->pages = kcalloc(device->pages, sizeof(*buffer->pages), GFP_KERNEL);
        buffer->descriptor = dma_alloc_coherent(&device->pci->dev, device->descriptor_bytes,
            &buffer->descriptor_address, GFP_KERNEL);
        if (!buffer->pages || !buffer->descriptor) goto failed;
        buffer->descriptor->cookie = cpu_to_le64(slot);
        segments = (void *)(buffer->descriptor + 1);
        for (page = 0; page < device->pages; ++page) {
            buffer->pages[page].cpu = dma_alloc_coherent(&device->pci->dev, PAGE_SIZE,
                &buffer->pages[page].address, GFP_KERNEL);
            if (!buffer->pages[page].cpu) goto failed;
            segments[page].address = cpu_to_le64(buffer->pages[page].address);
            segments[page].length = cpu_to_le32(PAGE_SIZE);
            segments[page].reserved = 0;
        }
    }
    return 0;
failed:
    free_buffers(device);
    return -ENOMEM;
}

static irqreturn_t chdma_interrupt(int irq, void *context)
{
    struct chdma_device *device = context;
    u32 events = readl(device->registers + CHDMA_REG_STATUS) & 0xf;
    struct chdma_completion completion;
    unsigned long flags;
    unsigned int slot;
    if (!events) return IRQ_NONE;
    spin_lock_irqsave(&device->lock, flags);
    /* Drain causes that arrive while an earlier completion is acknowledged.
     * This also avoids depending on a second edge for an already asserted
     * INTx line in gem5's minimal interrupt model. Doorbells take this same
     * lock, so only the bounded, already-posted receive queue can advance. */
    do {
        if (events & CHDMA_IRQ_RESET) {
            device->reset_done = true;
            device->faulted = false;
            kfifo_reset(&device->completions);
            if (device->buffers)
                for (slot = 0; slot < device->slots; ++slot)
                    device->buffers[slot].state = BUFFER_FREE;
        }
        if (events & CHDMA_IRQ_RX) {
            u64 cookie = readq(device->registers + CHDMA_REG_RX_COOKIE);
            u32 length = readl(device->registers + CHDMA_REG_RX_LENGTH);
            if (cookie >= device->rx_slots || length > device->bytes || !device->buffers ||
                device->buffers[cookie].state != BUFFER_RX) {
                device->faulted = true;
            } else {
                device->buffers[cookie].state = BUFFER_READY;
                completion = (struct chdma_completion){CHDMA_IRQ_RX, cookie, length, 0};
                if (!kfifo_put(&device->completions, completion)) device->faulted = true;
            }
        }
        if (events & CHDMA_IRQ_TX) {
            u64 cookie = readq(device->registers + CHDMA_REG_TX_COOKIE);
            if (cookie < device->rx_slots || cookie >= device->slots || !device->buffers ||
                device->buffers[cookie].state != BUFFER_TX) {
                device->faulted = true;
            } else {
                device->buffers[cookie].state = BUFFER_FREE;
                completion = (struct chdma_completion){CHDMA_IRQ_TX, cookie, 0, 0};
                if (!kfifo_put(&device->completions, completion)) device->faulted = true;
            }
        }
        if (events & CHDMA_IRQ_ERROR) {
            device->faulted = true;
            completion = (struct chdma_completion){CHDMA_IRQ_ERROR, 0, 0,
                readl(device->registers + CHDMA_REG_ERROR)};
            if (!kfifo_put(&device->completions, completion)) device->faulted = true;
        }
        writel(events, device->registers + CHDMA_REG_ACK);
        events = readl(device->registers + CHDMA_REG_STATUS) & 0xf;
    } while (events);
    spin_unlock_irqrestore(&device->lock, flags);
    wake_up_all(&device->wait);
    return IRQ_HANDLED;
}

/* Caller holds mutex; interrupts retain page ownership until all DMA drains. */
static int reset_device(struct chdma_device *device)
{
    WRITE_ONCE(device->reset_done, false);
    writel(1, device->registers + CHDMA_REG_RESET);
    if (!wait_event_timeout(device->wait, READ_ONCE(device->reset_done), 5 * HZ)) {
        device->safe_to_free = false;
        device->faulted = true;
        pci_clear_master(device->pci);
        dev_err(&device->pci->dev, "DMA reset timed out; retain buffers until VM teardown\n");
        return -ETIMEDOUT;
    }
    return 0;
}

static int chdma_open(struct inode *inode, struct file *file)
{
    struct chdma_device *device = container_of(file->private_data, struct chdma_device, misc);
    int result;
    if (mutex_lock_interruptible(&device->mutex)) return -ERESTARTSYS;
    if (device->removed) { result = -ENODEV; goto out; }
    if (device->opened || !device->safe_to_free) { result = -EBUSY; goto out; }
    result = allocate_buffers(device);
    if (result) goto out;
    result = reset_device(device);
    if (result) { free_buffers(device); goto out; }
    device->opened = true;
    file->private_data = device;
    kref_get(&device->references);
out:
    mutex_unlock(&device->mutex);
    return result;
}

static int chdma_release(struct inode *inode, struct file *file)
{
    struct chdma_device *device = file->private_data;
    mutex_lock(&device->mutex);
    if (!device->removed) reset_device(device);
    free_buffers(device);
    device->opened = false;
    mutex_unlock(&device->mutex);
    kref_put(&device->references, release_device);
    return 0;
}

static long chdma_ioctl(struct file *file, unsigned int command, unsigned long argument)
{
    struct chdma_device *device = file->private_data;
    struct chdma_submit request;
    struct chdma_info info;
    struct chdma_buffer *buffer;
    unsigned long flags;
    int result = 0;
    if (mutex_lock_interruptible(&device->mutex)) return -ERESTARTSYS;
    if (device->removed) { result = -ENODEV; goto out; }
    if (command == CHDMA_IOC_INFO) {
        info = (struct chdma_info){CHDMA_ABI_VERSION, device->rx_slots,
            device->slots - device->rx_slots, device->bytes, PAGE_SIZE, 0};
        if (copy_to_user((void __user *)argument, &info, sizeof(info))) result = -EFAULT;
        goto out;
    }
    if (command == CHDMA_IOC_RESET) { result = reset_device(device); goto out; }
    if (command != CHDMA_IOC_TX && command != CHDMA_IOC_RX) { result = -ENOTTY; goto out; }
    if (copy_from_user(&request, (void __user *)argument, sizeof(request))) { result = -EFAULT; goto out; }
    if (request.slot >= device->slots || request.length > device->bytes ||
        (command == CHDMA_IOC_RX && request.slot >= device->rx_slots) ||
        (command == CHDMA_IOC_TX && request.slot < device->rx_slots)) {
        result = -EINVAL; goto out;
    }
    spin_lock_irqsave(&device->lock, flags);
    buffer = &device->buffers[request.slot];
    if (device->faulted) { result = -EIO; goto unlock; }
    if (buffer->state != BUFFER_FREE && !(command == CHDMA_IOC_RX && buffer->state == BUFFER_USER)) {
        result = -EBUSY; goto unlock;
    }
    if (command == CHDMA_IOC_TX && (readl(device->registers + CHDMA_REG_STATUS) & CHDMA_STATUS_TX_BUSY)) {
        result = -EAGAIN; goto unlock;
    }
    buffer->descriptor->length = cpu_to_le32(command == CHDMA_IOC_RX ? device->bytes : request.length);
    buffer->descriptor->segments = cpu_to_le32(DIV_ROUND_UP(
        command == CHDMA_IOC_RX ? device->bytes : request.length, PAGE_SIZE));
    buffer->state = command == CHDMA_IOC_RX ? BUFFER_RX : BUFFER_TX;
    dma_wmb();
    writeq(buffer->descriptor_address, device->registers +
        (command == CHDMA_IOC_RX ? CHDMA_REG_RX_POST : CHDMA_REG_TX_POST));
unlock:
    spin_unlock_irqrestore(&device->lock, flags);
out:
    mutex_unlock(&device->mutex);
    return result;
}

static ssize_t chdma_read(struct file *file, char __user *destination, size_t bytes, loff_t *position)
{
    struct chdma_device *device = file->private_data;
    struct chdma_completion completion;
    unsigned long flags;
    int result;
    if (bytes < sizeof(completion)) return -EINVAL;
    if (mutex_lock_interruptible(&device->mutex)) return -ERESTARTSYS;
    if (device->removed) { result = -ENODEV; goto out; }
    spin_lock_irqsave(&device->lock, flags);
    result = kfifo_peek(&device->completions, &completion);
    spin_unlock_irqrestore(&device->lock, flags);
    if (!result) { result = device->faulted ? -EIO : -EAGAIN; goto out; }
    dma_rmb();
    if (copy_to_user(destination, &completion, sizeof(completion))) { result = -EFAULT; goto out; }
    spin_lock_irqsave(&device->lock, flags);
    kfifo_skip(&device->completions);
    if (completion.events == CHDMA_IRQ_RX)
        device->buffers[completion.slot].state = BUFFER_USER;
    spin_unlock_irqrestore(&device->lock, flags);
    result = sizeof(completion);
out:
    mutex_unlock(&device->mutex);
    return result;
}

static __poll_t chdma_poll(struct file *file, poll_table *wait)
{
    struct chdma_device *device = file->private_data;
    __poll_t result = 0;
    unsigned long flags;
    poll_wait(file, &device->wait, wait);
    spin_lock_irqsave(&device->lock, flags);
    if (!kfifo_is_empty(&device->completions)) result |= EPOLLIN | EPOLLRDNORM;
    if (device->faulted || device->removed) result |= EPOLLERR;
    if (device->removed) result |= EPOLLHUP;
    spin_unlock_irqrestore(&device->lock, flags);
    return result;
}

static int chdma_mmap(struct file *file, struct vm_area_struct *area)
{
    struct chdma_device *device = file->private_data;
    unsigned long slot = area->vm_pgoff;
    unsigned int page;
    int result = 0;
    if (mutex_lock_interruptible(&device->mutex)) return -ERESTARTSYS;
    if (device->removed) { result = -ENODEV; goto out; }
    if (slot >= device->slots || area->vm_end - area->vm_start != device->bytes ||
        !(area->vm_flags & VM_SHARED) || (area->vm_flags & VM_EXEC)) {
        result = -EINVAL; goto out;
    }
    /* x86 coherent PCI pages have normal WB CPU mappings. Each allocation is
     * order zero; vm_insert_page retains page refs across the userspace VMA.
     * The VMA retains this file, so release/reset precedes the final DMA free. */
    area->vm_flags |= VM_DONTEXPAND | VM_DONTDUMP | VM_MIXEDMAP;
    for (page = 0; page < device->pages; ++page) {
        result = vm_insert_page(area, area->vm_start + page * PAGE_SIZE,
            virt_to_page(device->buffers[slot].pages[page].cpu));
        if (result) break;
    }
out:
    mutex_unlock(&device->mutex);
    return result;
}

static const struct file_operations chdma_operations = {
    .owner = THIS_MODULE, .open = chdma_open, .release = chdma_release,
    .unlocked_ioctl = chdma_ioctl, .compat_ioctl = chdma_ioctl,
    .read = chdma_read, .poll = chdma_poll, .mmap = chdma_mmap, .llseek = no_llseek,
};

static int chdma_probe(struct pci_dev *pci, const struct pci_device_id *identity)
{
    struct chdma_device *device;
    int result;
    if (PAGE_SIZE != CHDMA_PAGE_BYTES || !buffer_mb || buffer_mb > 32 ||
        !receive_slots || receive_slots > 64 || !transmit_slots || transmit_slots > 64 ||
        receive_slots + transmit_slots > 64) return -EINVAL;
    device = kzalloc(sizeof(*device), GFP_KERNEL);
    if (!device) return -ENOMEM;
    kref_init(&device->references);
    device->pci = pci_dev_get(pci);
    mutex_init(&device->mutex);
    spin_lock_init(&device->lock);
    init_waitqueue_head(&device->wait);
    INIT_KFIFO(device->completions);
    device->safe_to_free = true;
    device->rx_slots = receive_slots;
    device->slots = receive_slots + transmit_slots;
    device->bytes = buffer_mb * 1024 * 1024;
    device->pages = device->bytes / PAGE_SIZE;
    device->descriptor_bytes = sizeof(struct chdma_descriptor) + device->pages * sizeof(struct chdma_segment);
    result = pci_enable_device(pci);
    if (result) goto failed;
    result = dma_set_mask_and_coherent(&pci->dev, DMA_BIT_MASK(64));
    if (result) goto disable;
    result = pci_request_regions(pci, "chimaera_dma");
    if (result) goto disable;
    device->registers = pci_iomap(pci, 0, 0);
    if (!device->registers) { result = -ENOMEM; goto regions; }
    if (readl(device->registers + CHDMA_REG_MAGIC) != CHDMA_MAGIC ||
        readl(device->registers + CHDMA_REG_VERSION) != CHDMA_ABI_VERSION) {
        result = -ENODEV; goto unmap;
    }
    pci_set_master(pci);
    result = request_irq(pci->irq, chdma_interrupt, IRQF_SHARED, "chimaera_dma", device);
    if (result) goto unmap;
    device->misc = (struct miscdevice){.minor = MISC_DYNAMIC_MINOR, .name = "chimaera-dma",
        .fops = &chdma_operations, .parent = &pci->dev, .mode = 0600};
    result = misc_register(&device->misc);
    if (result) goto irq;
    pci_set_drvdata(pci, device);
    dev_info(&pci->dev, "Chimaera coherent DMA ABI %u, IRQ %u\n", CHDMA_ABI_VERSION, pci->irq);
    return 0;
irq:
    free_irq(pci->irq, device);
unmap:
    pci_iounmap(pci, device->registers);
regions:
    pci_release_regions(pci);
disable:
    pci_disable_device(pci);
failed:
    kref_put(&device->references, release_device);
    return result;
}

static void chdma_remove(struct pci_dev *pci)
{
    struct chdma_device *device = pci_get_drvdata(pci);
    misc_deregister(&device->misc);
    mutex_lock(&device->mutex);
    reset_device(device);
    device->removed = true;
    free_irq(pci->irq, device);
    pci_clear_master(pci);
    pci_iounmap(pci, device->registers);
    device->registers = NULL;
    mutex_unlock(&device->mutex);
    wake_up_interruptible(&device->wait);
    pci_release_regions(pci);
    pci_disable_device(pci);
    kref_put(&device->references, release_device);
}

static const struct pci_device_id chdma_ids[] = {
    {PCI_DEVICE(CHDMA_VENDOR_ID, CHDMA_DEVICE_ID)}, {}
};
MODULE_DEVICE_TABLE(pci, chdma_ids);
static struct pci_driver chdma_driver = {
    .name = "chimaera_dma", .id_table = chdma_ids, .probe = chdma_probe, .remove = chdma_remove,
};
module_pci_driver(chdma_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Chimaera coherent DMA and interrupt transport");
