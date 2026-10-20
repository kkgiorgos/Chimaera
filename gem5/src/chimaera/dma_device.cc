/* SPDX-License-Identifier: BSD-3-Clause */
#include "chimaera/dma_device.hh"
#include "debug/ChimaeraDma.hh"

#include <algorithm>
#include <cstring>
#include <limits>

#include "mem/packet_access.hh"
#include "params/ChimaeraDma.hh"
#include "sim/byteswap.hh"
#include "sim/system.hh"

namespace gem5 { namespace chimaera {

ChimaeraDma::ChimaeraDma(const Params& p)
    : PciEndpoint(p), loopback(p.loopback), h2gPath(p.h2g_mailbox),
      g2hPath(p.g2h_mailbox), maxPending(p.max_pending), mailboxSlots(p.mailbox_slots),
      txHeaderDone([this] { headerDone(true); }, name() + ".txHeader"),
      rxHeaderDone([this] { headerDone(false); }, name() + ".rxHeader"),
      txSegmentsDone([this] { segmentsDone(true); }, name() + ".txSegments"),
      rxSegmentsDone([this] { segmentsDone(false); }, name() + ".rxSegments")
{
    fatal_if(maxPending == 0 || maxPending > 64,
             "Chimaera DMA max_pending must be between 1 and 64");
    fatal_if(mailboxSlots == 0 || mailboxSlots > 64, "invalid DMA mailbox slot count");
}

bool
ChimaeraDma::validMemory(Addr address, std::size_t length) const
{
    return length == 0 || (address <= std::numeric_limits<Addr>::max() - length + 1 &&
        sys->isMemAddr(address) && sys->isMemAddr(address + length - 1));
}

void
ChimaeraDma::updateInterrupt()
{
    DPRINTF(ChimaeraDma, "IRQ causes=%#x asserted=%d command=%#x\n",
            interrupts, irqAsserted, letoh(config().command));
    auto status = letoh(config().status);
    status = interrupts ? status | 8 : status & ~8;
    config().status = htole(static_cast<uint16_t>(status));
    const bool asserted = interrupts && !(letoh(config().command) & 0x400);
    if (asserted == irqAsserted) return;
    // Posting migrates to the destination event queue. A KVM CPU can already
    // acknowledge the device before intrPost returns. Publish our line state
    // first so a nested ACK clears it, and never overwrite that ACK afterwards.
    irqAsserted = asserted;
    if (asserted) intrPost();
    else intrClear();
}

void
ChimaeraDma::fail(std::uint32_t code)
{
    error = code;
    interrupts |= CHDMA_IRQ_ERROR;
    updateInterrupt();
}

void
ChimaeraDma::ingress()
{
    DPRINTF(ChimaeraDma, "INGRESS irq=%#x tx=%d/%u rx=%d/%u posted=%u\n",
            interrupts, tx.active, tx.stage, rx.active, rx.stage, postedRx.size());
    if (resetting) return;
    if (!loopback && !h2g) {
        h2g = std::make_unique<chimaera_mailbox::Mailbox>(h2gPath, false, mailboxSlots);
        g2h = std::make_unique<chimaera_mailbox::Mailbox>(g2hPath, false, mailboxSlots);
    }
    tryTx();
    tryRx();
}

void
ChimaeraDma::tryTx()
{
    if (!tx.active || resetting) return;
    if (tx.stage == 0) {
        if (!validMemory(pciToDma(tx.descriptorAddress), sizeof(tx.descriptor))) {
            tx.active = false;
            fail(CHDMA_ERROR_DESCRIPTOR);
            return;
        }
        tx.stage = 1;
        dmaRead(pciToDma(tx.descriptorAddress), sizeof(tx.descriptor), &txHeaderDone,
                reinterpret_cast<uint8_t*>(&tx.descriptor));
    } else if (tx.stage == 2) {
        try {
            if (loopback) {
                if (echoed.size() >= maxPending) return;
                echoTx.resize(tx.length);
                tx.data = echoTx.data();
            } else {
                if (!g2h) return;
                tx.data = g2h->reserve(tx.length);
                if (!tx.data) return; // Backpressure until the next host boundary.
            }
            tx.segments.resize(letoh(tx.descriptor.segments));
            tx.stage = 3;
            if (tx.segments.empty()) { payloadDone(true); return; }
            const auto size = tx.segments.size() * sizeof(chdma_segment);
            if (!validMemory(pciToDma(tx.descriptorAddress + sizeof(tx.descriptor)), size)) {
                tx.active = false;
                fail(CHDMA_ERROR_DESCRIPTOR);
                return;
            }
            dmaRead(pciToDma(tx.descriptorAddress + sizeof(tx.descriptor)), size,
                    &txSegmentsDone, reinterpret_cast<uint8_t*>(tx.segments.data()));
        } catch (const std::exception&) { tx.active = false; fail(CHDMA_ERROR_MAILBOX); }
    }
}

void
ChimaeraDma::tryRx()
{
    if (resetting || rx.active || (interrupts & CHDMA_IRQ_RX) || postedRx.empty()) return;
    try {
        if (loopback) {
            if (echoed.empty()) return;
            rx.data = echoed.front().data();
            rx.length = echoed.front().size();
        } else {
            if (!h2g) return;
            chimaera_mailbox::Mailbox::View view{};
            if (!h2g->peek(view)) return;
            rx.data = const_cast<uint8_t*>(view.data);
            rx.length = view.size;
        }
        rx.active = true;
        rx.stage = 1;
        rx.descriptorAddress = postedRx.front();
        postedRx.pop_front();
        if (!validMemory(pciToDma(rx.descriptorAddress), sizeof(rx.descriptor))) {
            rx.active = false;
            fail(CHDMA_ERROR_DESCRIPTOR);
            return;
        }
        dmaRead(pciToDma(rx.descriptorAddress), sizeof(rx.descriptor), &rxHeaderDone,
                reinterpret_cast<uint8_t*>(&rx.descriptor));
    } catch (const std::exception&) { rx.active = false; fail(CHDMA_ERROR_MAILBOX); }
}

void
ChimaeraDma::headerDone(bool transmit)
{
    auto& transfer = transmit ? tx : rx;
    if (resetting) { transfer.active = false; finishReset(); return; }
    const auto length = letoh(transfer.descriptor.length);
    const auto count = letoh(transfer.descriptor.segments);
    DPRINTF(ChimaeraDma, "%s HEADER cookie=%llu bytes=%u sg=%u\n",
            transmit ? "TX" : "RX", letoh(transfer.descriptor.cookie), length, count);
    if (length > CHDMA_MAX_BYTES || count > CHDMA_MAX_SEGMENTS ||
        count != (length + CHDMA_PAGE_BYTES - 1) / CHDMA_PAGE_BYTES ||
        (!transmit && length < transfer.length)) {
        transfer.active = false;
        fail(CHDMA_ERROR_DESCRIPTOR);
        return;
    }
    if (transmit) {
        transfer.length = length;
        transfer.stage = 2;
        tryTx();
    } else {
        // RX buffers can be large. Fetch only the page descriptors required by
        // this packet, rather than an image-sized buffer's complete SG table.
        transfer.segments.resize((transfer.length + CHDMA_PAGE_BYTES - 1) / CHDMA_PAGE_BYTES);
        transfer.stage = 3;
        if (transfer.segments.empty()) { payloadDone(false); return; }
        const auto size = transfer.segments.size() * sizeof(chdma_segment);
        if (!validMemory(pciToDma(transfer.descriptorAddress + sizeof(transfer.descriptor)), size)) {
            transfer.active = false;
            fail(CHDMA_ERROR_DESCRIPTOR);
            return;
        }
        dmaRead(pciToDma(transfer.descriptorAddress + sizeof(transfer.descriptor)), size,
                &rxSegmentsDone, reinterpret_cast<uint8_t*>(transfer.segments.data()));
    }
}

void
ChimaeraDma::segmentsDone(bool transmit)
{
    auto& transfer = transmit ? tx : rx;
    DPRINTF(ChimaeraDma, "%s PAYLOAD_BEGIN cookie=%llu bytes=%u\n",
            transmit ? "TX" : "RX", letoh(transfer.descriptor.cookie), transfer.length);
    if (resetting) { transfer.active = false; finishReset(); return; }
    // Validate the entire scatter list before issuing any payload request.
    for (const auto& segment : transfer.segments) {
        if (letoh(segment.length) != CHDMA_PAGE_BYTES || segment.reserved != 0 ||
            !validMemory(pciToDma(letoh(segment.address)), CHDMA_PAGE_BYTES)) {
            transfer.active = false;
            fail(CHDMA_ERROR_DESCRIPTOR);
            return;
        }
    }
    transfer.stage = 4;
    transfer.pending = transfer.segments.size();
    std::size_t offset = 0;
    for (const auto& segment : transfer.segments) {
        const auto length = std::min<std::size_t>(CHDMA_PAGE_BYTES, transfer.length - offset);
        auto* done = new EventFunctionWrapper([this, transmit] {
            auto& transfer = transmit ? tx : rx;
            if (--transfer.pending == 0) payloadDone(transmit);
        }, name() + ".payload", true);
        if (transmit)
            dmaRead(pciToDma(letoh(segment.address)), length, done, transfer.data + offset);
        else
            dmaWrite(pciToDma(letoh(segment.address)), length, done, transfer.data + offset);
        offset += length;
    }
}

void
ChimaeraDma::payloadDone(bool transmit)
{
    auto& transfer = transmit ? tx : rx;
    DPRINTF(ChimaeraDma, "%s COMPLETE cookie=%llu bytes=%u\n",
            transmit ? "TX" : "RX", letoh(transfer.descriptor.cookie), transfer.length);
    if (resetting) { transfer.active = false; finishReset(); return; }
    if (transmit) {
        if (loopback) echoed.push_back(std::move(echoTx));
        else g2h->commit(transfer.length);
        txCookie = letoh(transfer.descriptor.cookie);
        interrupts |= CHDMA_IRQ_TX;
    } else {
        rxLength = transfer.length;
        rxCookie = letoh(transfer.descriptor.cookie);
        if (loopback) echoed.pop_front();
        else h2g->pop();
        interrupts |= CHDMA_IRQ_RX;
    }
    transfer.active = false;
    updateInterrupt();
    tryTx();
    tryRx();
}

void
ChimaeraDma::serialize(CheckpointOut& cp) const
{
    fatal("Chimaera DMA requires a fresh boot; checkpointing is not supported");
}

void
ChimaeraDma::unserialize(CheckpointIn& cp)
{
    fatal("Chimaera DMA requires a fresh boot; checkpoint restore is not supported");
}

void
ChimaeraDma::reset()
{
    resetting = true;
    postedRx.clear();
    // A TX descriptor waiting for a mailbox slot has no outstanding memory
    // requests. All other stages must complete before reset releases buffers.
    if (tx.active && (tx.stage == 0 || tx.stage == 2)) tx.active = false;
    finishReset();
}

void
ChimaeraDma::finishReset()
{
    if (!resetting || tx.active || rx.active) return;
    echoed.clear();
    echoTx.clear();
    if (h2g) {
        chimaera_mailbox::Mailbox::View view{};
        while (h2g->peek(view)) h2g->pop();
    }
    h2g.reset();
    g2h.reset();
    tx = Transfer{};
    rx = Transfer{};
    error = 0;
    rxLength = 0;
    resetting = false;
    interrupts = CHDMA_IRQ_RESET;
    updateInterrupt();
}

Tick
ChimaeraDma::writeConfig(PacketPtr packet)
{
    const auto delay = PciEndpoint::writeConfig(packet);
    updateInterrupt();
    return delay;
}

Tick
ChimaeraDma::readDevice(PacketPtr packet)
{
    int bar;
    Addr offset;
    panic_if(!getBAR(packet->getAddr(), bar, offset) || bar != 0, "invalid DMA BAR");
    if ((offset == CHDMA_REG_RX_COOKIE || offset == CHDMA_REG_TX_COOKIE) && packet->getSize() == 8) {
        packet->setLE<uint64_t>(offset == CHDMA_REG_RX_COOKIE ? rxCookie : txCookie);
    } else if (packet->getSize() == 4) {
        uint32_t value = 0;
        switch (offset) {
          case CHDMA_REG_MAGIC: value = CHDMA_MAGIC; break;
          case CHDMA_REG_VERSION: value = CHDMA_ABI_VERSION; break;
          case CHDMA_REG_STATUS:
            value = interrupts | (tx.active ? CHDMA_STATUS_TX_BUSY : 0) |
                (rx.active ? CHDMA_STATUS_RX_BUSY : 0) |
                (resetting ? CHDMA_STATUS_RESETTING : 0);
            break;
          case CHDMA_REG_RX_LENGTH: value = rxLength; break;
          case CHDMA_REG_ERROR: value = error; break;
          default: fail(CHDMA_ERROR_DESCRIPTOR); break;
        }
        packet->setLE<uint32_t>(value);
    } else {
        std::memset(packet->getPtr<uint8_t>(), 0, packet->getSize());
        fail(CHDMA_ERROR_DESCRIPTOR);
    }
    packet->makeAtomicResponse();
    return pioDelay;
}

Tick
ChimaeraDma::writeDevice(PacketPtr packet)
{
    int bar;
    Addr offset;
    panic_if(!getBAR(packet->getAddr(), bar, offset) || bar != 0, "invalid DMA BAR");
    if (offset == CHDMA_REG_RX_POST && packet->getSize() == 8) {
        if (resetting || postedRx.size() >= maxPending) fail(CHDMA_ERROR_QUEUE_FULL);
        else { postedRx.push_back(packet->getLE<uint64_t>()); tryRx(); }
    } else if (offset == CHDMA_REG_TX_POST && packet->getSize() == 8) {
        if (resetting || tx.active || (interrupts & CHDMA_IRQ_TX)) fail(CHDMA_ERROR_BUSY);
        else {
            tx = Transfer{};
            tx.active = true;
            tx.descriptorAddress = packet->getLE<uint64_t>();
            tryTx();
        }
    } else if (offset == CHDMA_REG_ACK && packet->getSize() == 4) {
        interrupts &= ~packet->getLE<uint32_t>();
        updateInterrupt();
        tryRx();
    } else if (offset == CHDMA_REG_RESET && packet->getSize() == 4) {
        reset();
    } else {
        fail(CHDMA_ERROR_DESCRIPTOR);
    }
    packet->makeAtomicResponse();
    return pioDelay;
}

} } // namespace gem5::chimaera
