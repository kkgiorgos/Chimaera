// SPDX-License-Identifier: GPL-2.0
#include <linux/bitops.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/hardirq.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/kref.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/pid.h>
#include <linux/poll.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/version.h>
#include <gem5/chimaera_m5.h>

#ifndef CONFIG_X86_64
#error "The Chimaera m5 ABI targets little-endian x86-64 with WB RAM"
#endif

static unsigned int control_slot_bytes = 64 * 1024;
module_param(control_slot_bytes, uint, 0444);
MODULE_PARM_DESC(control_slot_bytes, "Bytes per control RX slot (page aligned)");
static unsigned int control_slots = 8;
module_param(control_slots, uint, 0444);
MODULE_PARM_DESC(control_slots, "Control RX slots per context");
/* An 8 MiB RTPS body also carries its 52-byte CDDS transport header. */
static unsigned int bulk_slot_bytes = 9 * 1024 * 1024;
module_param(bulk_slot_bytes, uint, 0444);
MODULE_PARM_DESC(bulk_slot_bytes, "Bytes per bulk RX slot (page aligned)");
static unsigned int bulk_slots = 4;
module_param(bulk_slots, uint, 0444);
MODULE_PARM_DESC(bulk_slots, "Bulk RX slots per context");

struct chm5_device;

struct chm5_context {
    struct chm5_device *device;
    struct pid *owner;
    wait_queue_head_t wait;
    struct page **pages;
    unsigned int id, process, lane;
    unsigned int slots, slot_bytes, pages_per_slot, nr_pages;
    u64 generation;
    unsigned int admission_waiters;
    bool registered;
};

struct chm5_device {
    struct pci_dev *pci;
    void __iomem *registers;
    struct miscdevice misc;
    struct kref references;
    /* Serializes file operations, context allocation and PCI removal. The IRQ
     * handler never takes this mutex, including during synchronous MMIO. */
    struct mutex mutex;
    spinlock_t context_lock;
    struct chm5_context *contexts[CHM5_MAX_CONTEXTS];
    bool removed;
};

static void __iomem *chm5_context_registers(struct chm5_context *context)
{
    return context->device->registers + CHM5_CONTEXT_BASE +
        context->id * CHM5_CONTEXT_STRIDE;
}

static void chm5_free_pages(struct chm5_context *context)
{
    unsigned int page;

    if (context->pages) {
        for (page = 0; page < context->nr_pages; ++page)
            if (context->pages[page])
                __free_page(context->pages[page]);
        kvfree(context->pages);
    }
    context->pages = NULL;
    context->nr_pages = 0;
    context->slots = 0;
    context->slot_bytes = 0;
    context->pages_per_slot = 0;
    context->generation = 0;
}

static void chm5_release_device(struct kref *reference)
{
    struct chm5_device *device = container_of(reference, struct chm5_device, references);

    pci_dev_put(device->pci);
    kfree(device);
}

/* Caller holds device->mutex and the BAR is still mapped. CLOSE synchronously
 * drops the model's physical references; the readback drains the PCI write
 * before any page can be freed. ACK only discards its sticky IRQ bit. */
static void chm5_close_context(struct chm5_context *context)
{
    void __iomem *registers;

    if (!context->registered)
        return;
    registers = chm5_context_registers(context);
    writel(1, registers + CHM5_CTX_CLOSE);
    (void)readq(registers + CHM5_CTX_GENERATION);
    writeq(BIT_ULL(context->id), context->device->registers + CHM5_REG_ACK_IRQ);
    context->registered = false;
    context->generation = 0;
}

/* Registration is a synchronous physical-memory handoff, not payload DMA.
 * All pages keep their kernel allocation references until final file release. */
static int chm5_register_context(struct chm5_context *context, unsigned int lane)
{
    struct chm5_registration *registration;
    struct chm5_device *device = context->device;
    void __iomem *registers = chm5_context_registers(context);
    u64 *physical_pages;
    size_t descriptor_bytes;
    unsigned int page;
    u32 error, status;
    int result = -ENOMEM;

    context->lane = lane;
    context->slots = lane == CHM5_CONTROL ? control_slots : bulk_slots;
    context->slot_bytes = lane == CHM5_CONTROL ? control_slot_bytes : bulk_slot_bytes;
    context->pages_per_slot = context->slot_bytes / PAGE_SIZE;
    context->nr_pages = context->slots * context->pages_per_slot;
    descriptor_bytes = sizeof(*registration) + context->nr_pages * sizeof(*physical_pages);

    /* kmalloc is deliberate: REGISTER consumes one physically contiguous
     * descriptor. Only the page-pointer bookkeeping may use vmalloc memory. */
    registration = kmalloc(descriptor_bytes, GFP_KERNEL);
    if (!registration)
        goto failed;
    context->pages = kvcalloc(context->nr_pages, sizeof(*context->pages), GFP_KERNEL);
    if (!context->pages)
        goto descriptor;
    *registration = (struct chm5_registration){
        .version = CHM5_ABI_VERSION,
        .size = sizeof(*registration),
        .context = context->id,
        .process = context->process,
        .lane = lane,
        .slots = context->slots,
        .slot_bytes = context->slot_bytes,
        .pages_per_slot = context->pages_per_slot,
    };
    physical_pages = (u64 *)(registration + 1);
    for (page = 0; page < context->nr_pages; ++page) {
        /* Normal order-zero RAM retains the default WB userspace mapping.
         * Zero it before exposure and never hand allocation ownership away. */
        context->pages[page] = alloc_page(GFP_KERNEL | __GFP_ZERO);
        if (!context->pages[page])
            goto descriptor;
        physical_pages[page] = page_to_phys(context->pages[page]);
    }

    wmb();
    context->registered = true;
    writeq(virt_to_phys(registration), device->registers + CHM5_REG_REGISTER);
    context->generation = readq(registers + CHM5_CTX_GENERATION);
    error = readl(registers + CHM5_CTX_ERROR);
    status = readl(registers + CHM5_CTX_STATUS);
    /* The generation readback also flushes REGISTER. The model has copied
     * both the header and page table, so the descriptor can now be freed. */
    if (!context->generation || error ||
        (status & (CHM5_STATUS_ERROR | CHM5_STATUS_SHUTDOWN))) {
        if (error && error <= MAX_ERRNO)
            result = -(int)error;
        else if (status & CHM5_STATUS_SHUTDOWN)
            result = -ESHUTDOWN;
        else
            result = -EIO;
        chm5_close_context(context);
        goto descriptor;
    }
    kfree(registration);
    return 0;

descriptor:
    kfree(registration);
failed:
    chm5_free_pages(context);
    return result;
}

static irqreturn_t chm5_interrupt(int irq, void *opaque)
{
    struct chm5_device *device = opaque;
    bool handled = false;
    unsigned int pass, id;

    if (READ_ONCE(device->removed))
        return IRQ_NONE;
    /* Bound draining even when a producer keeps notifying. Readiness stays
     * in the context's level status after the sticky IRQ mask is ACKed. */
    for (pass = 0; pass < 4; ++pass) {
        u64 mask = readq(device->registers + CHM5_REG_IRQ);

        if (!mask)
            break;
        handled = true;
        writeq(mask, device->registers + CHM5_REG_ACK_IRQ);
        for (id = 0; id < CHM5_MAX_CONTEXTS; ++id) {
            struct chm5_context *context;
            unsigned long flags;

            if (!(mask & BIT_ULL(id)))
                continue;
            spin_lock_irqsave(&device->context_lock, flags);
            context = device->contexts[id];
            spin_unlock_irqrestore(&device->context_lock, flags);
            /* Release unpublishes the pointer and synchronizes this IRQ
             * before freeing it. Never hold context_lock over MMIO or wakes:
             * a synchronous model callback may assert another interrupt. */
            if (context)
                wake_up_interruptible(&context->wait);
        }
    }
    return handled ? IRQ_HANDLED : IRQ_NONE;
}

static int chm5_open(struct inode *inode, struct file *file)
{
    struct chm5_device *device = container_of(file->private_data, struct chm5_device, misc);
    struct chm5_context *context;
    unsigned long flags;
    unsigned int id;
    int result;

    result = nonseekable_open(inode, file);
    if (result)
        return result;
    context = kzalloc(sizeof(*context), GFP_KERNEL);
    if (!context)
        return -ENOMEM;
    init_waitqueue_head(&context->wait);
    context->device = device;
    context->owner = get_task_pid(current, PIDTYPE_TGID);
    context->process = task_tgid_vnr(current);
    if (mutex_lock_interruptible(&device->mutex)) {
        result = -ERESTARTSYS;
        goto failed;
    }
    if (device->removed) {
        result = -ENODEV;
        goto unlock;
    }
    for (id = 0; id < CHM5_MAX_CONTEXTS; ++id)
        if (!device->contexts[id])
            break;
    if (id == CHM5_MAX_CONTEXTS) {
        result = -ENOSPC;
        goto unlock;
    }
    context->id = id;
    kref_get(&device->references);
    spin_lock_irqsave(&device->context_lock, flags);
    device->contexts[id] = context;
    spin_unlock_irqrestore(&device->context_lock, flags);
    file->private_data = context;
    mutex_unlock(&device->mutex);
    return 0;

unlock:
    mutex_unlock(&device->mutex);
failed:
    put_pid(context->owner);
    kfree(context);
    return result;
}

static int chm5_release(struct inode *inode, struct file *file)
{
    struct chm5_context *context = file->private_data;
    struct chm5_device *device = context->device;
    unsigned long flags;

    mutex_lock(&device->mutex);
    if (!device->removed)
        chm5_close_context(context);
    spin_lock_irqsave(&device->context_lock, flags);
    device->contexts[context->id] = NULL;
    spin_unlock_irqrestore(&device->context_lock, flags);
    if (!device->removed)
        synchronize_irq(device->pci->irq);
    /* VMAs retain this file. Final release cannot run while a userspace loan
     * still maps its pages. Removal has already unregistered every context. */
    chm5_free_pages(context);
    put_pid(context->owner);
    kfree(context);
    /* New opens cannot reuse the ID until cleanup finishes under the mutex. */
    mutex_unlock(&device->mutex);
    kref_put(&device->references, chm5_release_device);
    return 0;
}

static bool chm5_admission_ready(struct chm5_context *context, u64 *epoch, int *error)
{
    struct chm5_device *device = context->device;
    void __iomem *registers;
    u32 status, code;
    bool ready = true;

    mutex_lock(&device->mutex);
    if (device->removed) {
        *error = -ENODEV;
    } else if (!context->registered) {
        *error = -ENXIO;
    } else {
        registers = chm5_context_registers(context);
        status = readl(registers + CHM5_CTX_STATUS);
        code = readl(registers + CHM5_CTX_ERROR);
        if (code || (status & CHM5_STATUS_ERROR))
            *error = code && code <= MAX_ERRNO ? -(int)code : -EIO;
        else if (status & CHM5_STATUS_SHUTDOWN)
            *error = -ESHUTDOWN;
        else {
            *epoch = readq(device->registers + CHM5_REG_ADMISSION_EPOCH);
            ready = *epoch != 0;
        }
    }
    mutex_unlock(&device->mutex);
    return ready;
}

static long chm5_wait_admission(struct chm5_context *context, unsigned long argument)
{
    struct chm5_device *device = context->device;
    u64 epoch = 0;
    int result, error = 0;

    if (mutex_lock_interruptible(&device->mutex))
        return -ERESTARTSYS;
    if (device->removed || !context->registered) {
        result = device->removed ? -ENODEV : -ENXIO;
        goto unlock;
    }
    if (!(readl(device->registers + CHM5_REG_FEATURES) & CHM5_FEATURE_ADMISSION_IRQ)) {
        result = -EOPNOTSUPP;
        goto unlock;
    }
    if (!context->admission_waiters++) {
        writel(1, chm5_context_registers(context) + CHM5_CTX_ARM_ADMISSION);
        (void)readq(chm5_context_registers(context) + CHM5_CTX_GENERATION);
    }
    mutex_unlock(&device->mutex);
    /* Install the wait before checking the persistent epoch. The IRQ handler
     * only wakes this queue; it neither clears the epoch nor takes mutex. */
    result = wait_event_interruptible(context->wait,
        chm5_admission_ready(context, &epoch, &error));
    mutex_lock(&device->mutex);
    if (!--context->admission_waiters && !device->removed && context->registered) {
        writel(0, chm5_context_registers(context) + CHM5_CTX_ARM_ADMISSION);
        (void)readq(chm5_context_registers(context) + CHM5_CTX_GENERATION);
    }
    mutex_unlock(&device->mutex);
    if (result)
        return result;
    if (error)
        return error;
    return copy_to_user((void __user *)argument, &epoch, sizeof(epoch)) ? -EFAULT : 0;

unlock:
    mutex_unlock(&device->mutex);
    return result;
}

static long chm5_ioctl(struct file *file, unsigned int command, unsigned long argument)
{
    struct chm5_context *context = file->private_data;
    struct chm5_device *device = context->device;
    struct chm5_bind bind;
    int result;

    if (command != CHM5_IOC_BIND && command != CHM5_IOC_WAIT_ADMISSION)
        return -ENOTTY;
    /* An inherited or transferred fd still belongs to its opening process.
     * Retain the pid object as well as the userspace getpid() value, so PID
     * reuse or a different PID namespace cannot impersonate that owner. */
    if (task_tgid(current) != context->owner || task_tgid_vnr(current) != context->process)
        return -EPERM;
    if (command == CHM5_IOC_WAIT_ADMISSION)
        return chm5_wait_admission(context, argument);
    if (copy_from_user(&bind, (void __user *)argument, sizeof(bind)))
        return -EFAULT;
    if (bind.version != CHM5_ABI_VERSION ||
        (bind.lane != CHM5_CONTROL && bind.lane != CHM5_BULK))
        return -EINVAL;
    if (mutex_lock_interruptible(&device->mutex))
        return -ERESTARTSYS;
    if (device->removed) {
        result = -ENODEV;
        goto out;
    }
    if (context->registered) {
        result = -EBUSY;
        goto out;
    }
    result = chm5_register_context(context, bind.lane);
    if (result)
        goto out;
    bind = (struct chm5_bind){
        .version = CHM5_ABI_VERSION,
        .lane = context->lane,
        .context = context->id,
        .process = context->process,
        .slots = context->slots,
        .slot_bytes = context->slot_bytes,
        .generation = context->generation,
    };
    if (copy_to_user((void __user *)argument, &bind, sizeof(bind))) {
        chm5_close_context(context);
        chm5_free_pages(context);
        result = -EFAULT;
    }
    wake_up_interruptible(&context->wait);
out:
    mutex_unlock(&device->mutex);
    return result;
}

static ssize_t chm5_read(struct file *file, char __user *destination, size_t bytes,
                        loff_t *position)
{
    return -EOPNOTSUPP;
}

static __poll_t chm5_poll(struct file *file, poll_table *wait)
{
    struct chm5_context *context = file->private_data;
    struct chm5_device *device = context->device;
    void __iomem *registers;
    u64 generation;
    u32 status, error;
    __poll_t result = 0;

    /* Install the waiter before reading level status, so ACK cannot lose a
     * readiness transition between checking the device and going to sleep. */
    poll_wait(file, &context->wait, wait);
    mutex_lock(&device->mutex);
    if (device->removed) {
        result = EPOLLHUP;
        goto out;
    }
    if (!context->registered) {
        result = EPOLLERR;
        goto out;
    }
    registers = chm5_context_registers(context);
    status = readl(registers + CHM5_CTX_STATUS);
    error = readl(registers + CHM5_CTX_ERROR);
    generation = readq(registers + CHM5_CTX_GENERATION);
    if (generation != context->generation)
        result |= EPOLLERR;
    else if (status & CHM5_STATUS_RX)
        result |= EPOLLIN | EPOLLRDNORM;
    if (error || (status & CHM5_STATUS_ERROR))
        result |= EPOLLERR;
    if (status & CHM5_STATUS_SHUTDOWN)
        result |= EPOLLHUP;
out:
    mutex_unlock(&device->mutex);
    return result;
}

static int chm5_mmap(struct file *file, struct vm_area_struct *area)
{
    struct chm5_context *context = file->private_data;
    struct chm5_device *device = context->device;
    unsigned long slot = area->vm_pgoff;
    unsigned int page;
    int result = 0;

    if (mutex_lock_interruptible(&device->mutex))
        return -ERESTARTSYS;
    if (device->removed) {
        result = -ENODEV;
        goto out;
    }
    if (!context->registered) {
        result = -ENXIO;
        goto out;
    }
    if (slot >= context->slots || area->vm_end - area->vm_start != context->slot_bytes ||
        !(area->vm_flags & VM_SHARED) || (area->vm_flags & VM_EXEC)) {
        result = -EINVAL;
        goto out;
    }
    /* pgoff selects a slot, rather than a page within one large mapping.
     * vm_insert_page adds VMA references without changing WB cacheability. */
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 3, 0)
    vm_flags_set(area, VM_DONTEXPAND | VM_DONTDUMP | VM_MIXEDMAP);
    vm_flags_clear(area, VM_MAYEXEC);
#else
    area->vm_flags |= VM_DONTEXPAND | VM_DONTDUMP | VM_MIXEDMAP;
    area->vm_flags &= ~VM_MAYEXEC;
#endif
    for (page = 0; page < context->pages_per_slot; ++page) {
        result = vm_insert_page(area, area->vm_start + page * PAGE_SIZE,
            context->pages[slot * context->pages_per_slot + page]);
        if (result)
            break;
    }
out:
    mutex_unlock(&device->mutex);
    return result;
}

static const struct file_operations chm5_operations = {
    .owner = THIS_MODULE,
    .open = chm5_open,
    .release = chm5_release,
    .unlocked_ioctl = chm5_ioctl,
    .compat_ioctl = chm5_ioctl,
    .read = chm5_read,
    .poll = chm5_poll,
    .mmap = chm5_mmap,
    .llseek = no_llseek,
};

static bool chm5_valid_geometry(unsigned int slots, unsigned int slot_bytes)
{
    return slots && slots <= CHM5_MAX_SLOTS && slot_bytes &&
        slot_bytes <= CHM5_MAX_BYTES && !(slot_bytes % PAGE_SIZE);
}

static int chm5_probe(struct pci_dev *pci, const struct pci_device_id *identity)
{
    struct chm5_device *device;
    int result;

    if (PAGE_SIZE != CHM5_PAGE_BYTES ||
        !chm5_valid_geometry(control_slots, control_slot_bytes) ||
        !chm5_valid_geometry(bulk_slots, bulk_slot_bytes))
        return -EINVAL;
    device = kzalloc(sizeof(*device), GFP_KERNEL);
    if (!device)
        return -ENOMEM;
    device->pci = pci_dev_get(pci);
    kref_init(&device->references);
    mutex_init(&device->mutex);
    spin_lock_init(&device->context_lock);
    result = pci_enable_device(pci);
    if (result)
        goto failed;
    result = pci_request_regions(pci, "chimaera_m5");
    if (result)
        goto disable;
    if (!(pci_resource_flags(pci, 0) & IORESOURCE_MEM) ||
        pci_resource_len(pci, 0) < CHM5_CONTEXT_BASE +
            CHM5_MAX_CONTEXTS * CHM5_CONTEXT_STRIDE) {
        result = -ENODEV;
        goto regions;
    }
    device->registers = pci_iomap(pci, 0, 0);
    if (!device->registers) {
        result = -ENOMEM;
        goto regions;
    }
    if (readl(device->registers + CHM5_REG_MAGIC) != CHM5_MAGIC ||
        readl(device->registers + CHM5_REG_VERSION) != CHM5_ABI_VERSION) {
        result = -ENODEV;
        goto unmap;
    }
    /* This device only signals readiness. It never bus-masters payloads. */
    pci_clear_master(pci);
    result = request_irq(pci->irq, chm5_interrupt, IRQF_SHARED, "chimaera_m5", device);
    if (result)
        goto unmap;
    device->misc = (struct miscdevice){
        .minor = MISC_DYNAMIC_MINOR,
        .name = "chimaera-m5",
        .fops = &chm5_operations,
        .parent = &pci->dev,
        .mode = 0600,
    };
    result = misc_register(&device->misc);
    if (result)
        goto irq;
    pci_set_drvdata(pci, device);
    dev_info(&pci->dev, "Chimaera m5 notification ABI %u, IRQ %u\n", CHM5_ABI_VERSION, pci->irq);
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
    kref_put(&device->references, chm5_release_device);
    return result;
}

static void chm5_remove(struct pci_dev *pci)
{
    struct chm5_device *device = pci_get_drvdata(pci);
    unsigned int id;

    /* misc_deregister serializes against opens before dropping the PCI
     * reference; existing files hold their own device krefs. */
    misc_deregister(&device->misc);
    mutex_lock(&device->mutex);
    WRITE_ONCE(device->removed, true);
    for (id = 0; id < CHM5_MAX_CONTEXTS; ++id) {
        struct chm5_context *context = device->contexts[id];

        if (!context)
            continue;
        chm5_close_context(context);
        wake_up_interruptible(&context->wait);
    }
    free_irq(pci->irq, device);
    pci_iounmap(pci, device->registers);
    device->registers = NULL;
    mutex_unlock(&device->mutex);
    pci_release_regions(pci);
    pci_disable_device(pci);
    pci_set_drvdata(pci, NULL);
    kref_put(&device->references, chm5_release_device);
}

static const struct pci_device_id chm5_ids[] = {
    {PCI_DEVICE(CHM5_VENDOR_ID, CHM5_DEVICE_ID)}, {}
};
MODULE_DEVICE_TABLE(pci, chm5_ids);

static struct pci_driver chm5_driver = {
    .name = "chimaera_m5",
    .id_table = chm5_ids,
    .probe = chm5_probe,
    .remove = chm5_remove,
};
module_pci_driver(chm5_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Chimaera m5 pinned RX pages and per-context readiness notifications");
