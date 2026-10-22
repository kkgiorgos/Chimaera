/* SPDX-License-Identifier: BSD-3-Clause */
#include "chimaera/m5_device.hh"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <limits>
#include <mutex>
#include <sstream>
#include <unistd.h>

#include "cpu/thread_context.hh"
#include "mem/packet_access.hh"
#include "mem/translating_port_proxy.hh"
#include "params/ChimaeraM5.hh"
#include "sim/byteswap.hh"
#include "sim/eventq.hh"
#include "sim/system.hh"

namespace gem5 { namespace chimaera {
namespace {
// Only for the simulation owner thread after all event queues have stopped.
// Unlike ScopedMigration, entry must not unlock the caller's unowned queue.
// Nested IRQ wakeups may migrate away and back, so retain ownership until the
// entire paused operation returns, then restore the caller's queue and unlock.
class PausedEventQueueGuard
{
    EventQueue* const previous;
    std::lock_guard<EventQueue> lock;

  public:
    explicit PausedEventQueueGuard(EventQueue* owner)
        : previous(curEventQueue()), lock(*owner)
    {
        curEventQueue(owner);
    }
    ~PausedEventQueueGuard()
    {
        curEventQueue(previous);
    }
    PausedEventQueueGuard(const PausedEventQueueGuard&) = delete;
    PausedEventQueueGuard& operator=(const PausedEventQueueGuard&) = delete;
};

constexpr size_t HeaderBytes = 52;
uint32_t read32(const uint8_t* p)
{
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 |
        uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
bool validFrame(const std::vector<uint8_t>& bytes, unsigned source)
{
    return bytes.size() >= HeaderBytes && bytes.size() <= CHM5_MAX_BYTES &&
        !std::memcmp(bytes.data(), "CDDS", 4) && bytes[4] == 1 &&
        bytes[5] == 3 && !bytes[6] && !bytes[7] && bytes[12] == source &&
        bytes[32] <= 2 && read32(bytes.data() + 48) == bytes.size() - HeaderBytes;
}
unsigned frameLane(const std::vector<uint8_t>& bytes)
{
    return (read32(bytes.data() + 28) & CHM5_BULK_PORT_BIT) ? CHM5_BULK : CHM5_CONTROL;
}
uint64_t errorResult(int error) { return static_cast<uint64_t>(-int64_t(error)); }
}

std::vector<ChimaeraM5*> ChimaeraM5::devices;

ChimaeraM5::ChimaeraM5(const Params& p)
    : PciEndpoint(p), h2gPath(p.h2g_mailbox), g2hPath(p.g2h_mailbox),
      mailboxSlots(p.mailbox_slots), maxPending(p.max_pending),
      ingressRecords(p.ingress_records), maxPendingBytes(p.max_pending_bytes),
      physical(sys->getSystemPort(), sys->cacheLineSize())
{
    fatal_if(!mailboxSlots || mailboxSlots > 64 || !maxPending || maxPending > 1024 ||
        !ingressRecords || ingressRecords > 1024 || maxPendingBytes < CHM5_MAX_BYTES,
        "Invalid ChimaeraM5 bounded queue geometry");
    for (auto* other : devices)
        fatal_if(other->sys == sys, "Only one ChimaeraM5 function per system is supported");
    devices.push_back(this);
}
ChimaeraM5::~ChimaeraM5()
{
    devices.erase(std::remove(devices.begin(), devices.end(), this), devices.end());
}

bool ChimaeraM5::validMemory(Addr addr, size_t length) const
{
    if (!length || addr > std::numeric_limits<Addr>::max() - length + 1) return false;
    // Validate every page, including discontiguous physical memory ranges.
    for (size_t offset = 0; offset < length;) {
        if (!sys->isMemAddr(addr + offset)) return false;
        const auto bytes = std::min<size_t>(CHM5_PAGE_BYTES -
            ((addr + offset) % CHM5_PAGE_BYTES), length - offset);
        if (!sys->isMemAddr(addr + offset + bytes - 1)) return false;
        offset += bytes;
    }
    return true;
}
uint32_t ChimaeraM5::status(const Context& c) const
{
    return (!c.ready.empty() ? CHM5_STATUS_RX : 0) |
        (c.waitAdmission && admissionEpoch ? CHM5_STATUS_ADMISSION : 0) |
        (c.error ? CHM5_STATUS_ERROR : 0) |
        ((!c.active || c.shutdown) ? CHM5_STATUS_SHUTDOWN : 0);
}
void ChimaeraM5::updateInterrupt()
{
    auto pciStatus = letoh(config().status);
    config().status = htole(uint16_t(irqPending ? pciStatus | 8 : pciStatus & ~8));
    const bool asserted = irqPending && !(letoh(config().command) & 0x400);
    if (asserted == irqAsserted) return;
    // intrPost can migrate queues and allow a nested ACK. Publish first.
    irqAsserted = asserted;
    if (asserted) intrPost(); else intrClear();
}
void ChimaeraM5::notify(unsigned index)
{
    ++contexts[index].notification;
    irqPending |= uint64_t{1} << index;
}
void ChimaeraM5::fail(unsigned index, unsigned error)
{
    contexts[index].error = error;
    notify(index);
}

void ChimaeraM5::registerContext(Addr addr)
{
    chm5_registration reg{};
    if (!validMemory(addr, sizeof(reg))) return;
    physical.readBlob(addr, &reg, sizeof(reg));
    const unsigned id = letoh(reg.context);
    if (id >= contexts.size()) return;
    auto& c = contexts[id];
    if (c.active) { fail(id, EBUSY); return; }
    const auto slots = letoh(reg.slots), bytes = letoh(reg.slot_bytes);
    const auto pages = letoh(reg.pages_per_slot);
    if (letoh(reg.version) != CHM5_ABI_VERSION || letoh(reg.size) != sizeof(reg) ||
        letoh(reg.lane) > CHM5_BULK || !letoh(reg.process) || !slots ||
        slots > CHM5_MAX_SLOTS || !bytes || bytes > CHM5_MAX_BYTES ||
        bytes % CHM5_PAGE_BYTES || pages != bytes / CHM5_PAGE_BYTES ||
        !validMemory(addr, sizeof(reg) + size_t(slots) * pages * sizeof(uint64_t))) {
        fail(id, EINVAL); return;
    }
    std::vector<uint64_t> addresses(size_t(slots) * pages);
    physical.readBlob(addr + sizeof(reg), addresses.data(), addresses.size() * sizeof(uint64_t));
    std::set<Addr> uniquePages;
    for (const auto& other : contexts)
        if (other.active)
            for (const auto& slot : other.slots)
                uniquePages.insert(slot.pages.begin(), slot.pages.end());
    for (auto address : addresses) {
        address = letoh(address);
        if (address % CHM5_PAGE_BYTES || !validMemory(address, CHM5_PAGE_BYTES) ||
            !uniquePages.insert(address).second) { fail(id, EFAULT); return; }
    }
    c = Context{};
    c.generation = ++nextGeneration;
    c.process = letoh(reg.process);
    c.lane = letoh(reg.lane);
    c.slotBytes = bytes;
    c.active = true;
    c.slots.resize(slots);
    for (unsigned slot = 0; slot < slots; ++slot)
        for (unsigned page = 0; page < pages; ++page)
            c.slots[slot].pages.push_back(letoh(addresses[slot * pages + page]));
    irqPending &= ~(uint64_t{1} << id);
}
void ChimaeraM5::closeContext(unsigned id)
{
    if (id >= contexts.size()) return;
    auto& c = contexts[id];
    // No deferred memory operations exist: return means every physical reference
    // has gone. Loan lifetime is enforced by the userspace owner/file mapping.
    c = Context{};
    c.shutdown = true;
    notify(id);
}

bool ChimaeraM5::canQueue(const Context& c, size_t length) const
{
    return c.pending.size() < maxPending && length <= maxPendingBytes - c.queuedBytes;
}
void ChimaeraM5::fill(Context& c)
{
    const unsigned id = &c - contexts.data();
    unsigned records = 0;
    while (!c.pending.empty() && records++ < CHM5_MAX_SLOTS) {
        auto slot = std::find_if(c.slots.begin(), c.slots.end(), [](const Slot& s) {
            return s.state == SlotState::posted;
        });
        if (slot == c.slots.end()) break;
        const auto& packet = c.pending.front();
        const auto& bytes = *packet.frame;
        if (bytes.size() > c.slotBytes) {
            fail(id, EMSGSIZE);
            c.queuedBytes -= bytes.size();
            c.pending.pop_front();
            continue;
        }
        size_t offset = 0;
        for (auto page : slot->pages) {
            const auto count = std::min<size_t>(CHM5_PAGE_BYTES, bytes.size() - offset);
            if (count) physical.writeBlob(page, bytes.data() + offset, count);
            offset += count;
            if (offset == bytes.size()) break;
        }
        slot->length = bytes.size();
        slot->epoch = packet.epoch;
        slot->sequence = packet.sequence;
        slot->state = SlotState::ready;
        c.ready.push_back(slot - c.slots.begin());
        c.queuedBytes -= bytes.size();
        c.pending.pop_front();
        notify(id); // Payload and completion fields precede IRQ publication.
    }
}
void ChimaeraM5::queue(Context& c, const Packet& packet)
{
    c.queuedBytes += packet.frame->size();
    c.pending.push_back(packet);
    fill(c);
}
std::vector<unsigned> ChimaeraM5::recipients(const std::vector<uint8_t>& bytes, unsigned lane) const
{
    std::vector<unsigned> result;
    const auto side = bytes[32];
    if (side && side != 2) return result;
    const auto process = read32(bytes.data() + 36);
    const auto sourceProcess = read32(bytes.data() + 16);
    const auto port = read32(bytes.data() + 28);
    for (unsigned id = 0; id < contexts.size(); ++id) {
        const auto& c = contexts[id];
        if (!c.active || c.shutdown || c.error || c.lane != lane ||
            !c.channels.count(port)) continue;
        if (bytes[12] == 2 && c.process == sourceProcess) continue;
        if (side) {
            if (c.process != process) continue;
            std::array<uint8_t, 16> address{};
            address[0] = 2;
            for (unsigned byte = 0; byte < 4; ++byte)
                address[4 + byte] = c.process >> (8 * byte);
            if (std::memcmp(bytes.data() + 32, address.data(), address.size())) continue;
        }
        result.push_back(id);
    }
    return result;
}
bool ChimaeraM5::deliver(const Packet& packet, unsigned lane, bool keepUnmatched)
{
    const auto targets = recipients(*packet.frame, lane);
    // Fast DDS probes metatraffic ports that need not have a receiver. Match
    // datagram/router semantics for these misses; retaining them indefinitely
    // would exhaust the deferred queue and block real control input. Routed
    // application ports keep their late-registration delivery guarantee.
    if (targets.empty() && keepUnmatched &&
        !(read32(packet.frame->data() + 28) & CHM5_APPLICATION_PORT_BIT)) return true;
    bool defer = targets.empty();
    if (keepUnmatched) {
        // An admitted frame may be waiting for registration or RX capacity.
        // Fresh ingress must join that destination's FIFO even if this smaller
        // frame could fit now. Other destinations can still be admitted.
        for (const auto& previous : lanes[lane].unmatched)
            if (!std::memcmp(previous.frame->data() + 28,
                    packet.frame->data() + 28, 20)) {
                defer = true;
                break;
            }
    }
    for (auto id : targets)
        if (!canQueue(contexts[id], packet.frame->size())) defer = true;
    if (defer) {
        if (!keepUnmatched) return false;
        auto& l = lanes[lane];
        if (l.unmatched.size() >= maxPending || packet.frame->size() >
            maxPendingBytes - l.unmatchedBytes) return false;
        l.unmatched.push_back(packet);
        l.unmatchedBytes += packet.frame->size();
        return true;
    }
    for (auto id : targets) queue(contexts[id], packet);
    return true;
}
void ChimaeraM5::retryUnmatched(unsigned lane)
{
    auto& l = lanes[lane];
    const size_t count = l.unmatched.size();
    // Rotate unmatched destinations, but never overtake an earlier record for
    // the same destination port/process. Each pass visits a bounded snapshot.
    std::set<std::array<uint8_t, 20>> blocked;
    for (size_t index = 0; index < count; ++index) {
        auto packet = l.unmatched.front();
        l.unmatched.pop_front();
        const auto& frame = *packet.frame;
        if (!(read32(frame.data() + 28) & CHM5_APPLICATION_PORT_BIT) &&
            recipients(frame, lane).empty()) {
            l.unmatchedBytes -= frame.size();
            continue;
        }
        std::array<uint8_t, 20> key{};
        std::copy_n(frame.data() + 28, key.size(), key.begin());
        if (!blocked.count(key) && !recipients(frame, lane).empty() && deliver(packet, lane, false)) {
            l.unmatchedBytes -= frame.size();
        } else {
            blocked.insert(key);
            l.unmatched.push_back(std::move(packet));
        }
    }
}
void ChimaeraM5::connectMailboxes(unsigned lane)
{
    auto& l = lanes[lane];
    const std::string suffix = lane ? ".bulk" : ".control";
    // Startup may precede owner creation. Failure is retryable only at the next
    // explicit boundary, never a host wait or a guest polling timer.
    if (!l.h2g && ::access((h2gPath + suffix).c_str(), F_OK) == 0)
        l.h2g = std::make_unique<chimaera_mailbox::Mailbox>(h2gPath + suffix, false, mailboxSlots);
    if (!l.g2h && ::access((g2hPath + suffix).c_str(), F_OK) == 0)
        l.g2h = std::make_unique<chimaera_mailbox::Mailbox>(g2hPath + suffix, false, mailboxSlots);
}
void ChimaeraM5::flushOutgoing(unsigned lane)
{
    auto& l = lanes[lane];
    if (!l.g2h) return;
    unsigned count = 0;
    while (!l.outgoing.empty() && count++ < ingressRecords) {
        const auto& frame = *l.outgoing.front().frame;
        auto* target = l.g2h->reserve(frame.size());
        if (!target) break;
        std::memcpy(target, frame.data(), frame.size());
        l.g2h->commit(frame.size());
        l.outgoingBytes -= frame.size();
        l.outgoing.pop_front();
    }
}
void ChimaeraM5::ingress()
{
    // Called after simulate() returned and every CPU event queue has stopped.
    // Establish a locked current queue before intrPost() can reach a nested
    // KVM wakeup migration. Its return reacquires this lock; the guard releases
    // it before returning to Python. dispatch() already owns its migrated lock
    // and must not use this paused-only guard.
    PausedEventQueueGuard owner(eventQueue());
    ++admissionEpoch;
    if (admissionEpoch == 1) {
        for (unsigned id = 0; id < contexts.size(); ++id)
            if (contexts[id].active && contexts[id].waitAdmission) notify(id);
    }
    for (unsigned lane = 0; lane < lanes.size(); ++lane) {
        auto& l = lanes[lane];
        try {
            connectMailboxes(lane);
            flushOutgoing(lane);
            retryUnmatched(lane);
            if (!l.h2g) continue;
            for (unsigned count = 0; count < ingressRecords; ++count) {
                chimaera_mailbox::Mailbox::View view{};
                if (!l.h2g->peek(view)) break;
                auto bytes = std::make_shared<std::vector<uint8_t>>(view.data, view.data + view.size);
                if (!validFrame(*bytes, 1) || frameLane(*bytes) != lane || (*bytes)[32] == 1) {
                    l.h2g->pop();
                    for (unsigned id = 0; id < contexts.size(); ++id)
                        if (contexts[id].active && contexts[id].lane == lane) fail(id, EPROTO);
                    continue;
                }
                const Packet packet{bytes, admissionEpoch, ++packetSequence};
                if (!deliver(packet, lane, true)) break;
                l.h2g->pop();
            }
        } catch (const std::exception& e) {
            warn("ChimaeraM5 lane %u mailbox error: %s", lane, e.what());
            for (unsigned id = 0; id < contexts.size(); ++id)
                if (contexts[id].active && contexts[id].lane == lane) fail(id, EIO);
        }
    }
    updateInterrupt();
}

uint64_t ChimaeraM5::flush_egress()
{
    // Same all-CPUs-paused owner-thread requirement as ingress(). This hook
    // permits a bounded host drain/flush cycle to export all accepted records
    // at the completed boundary, without advancing admission or consuming h2g.
    uint64_t remaining = 0;
    for (unsigned lane = 0; lane < lanes.size(); ++lane) {
        auto& l = lanes[lane];
        const std::string suffix = lane ? ".bulk" : ".control";
        if (!l.g2h && ::access((g2hPath + suffix).c_str(), F_OK) == 0)
            l.g2h = std::make_unique<chimaera_mailbox::Mailbox>(
                g2hPath + suffix, false, mailboxSlots);
        flushOutgoing(lane);
        remaining += l.outgoing.size();
    }
    return remaining;
}

std::string ChimaeraM5::diagnostics()
{
    // Read-only inspection by the paused simulation owner; never called from
    // guest payload operations or enabled by the performance configuration.
    std::ostringstream out;
    out << "epoch=" << admissionEpoch << " irq=" << irqPending
        << " asserted=" << irqAsserted << " pci_command=" << letoh(config().command);
    for (unsigned lane = 0; lane < lanes.size(); ++lane) {
        const auto& l = lanes[lane];
        out << " lane" << lane << "(out=" << l.outgoing.size()
            << ",deferred=" << l.unmatched.size() << ')';
        if (!l.unmatched.empty()) {
            const auto& frame = *l.unmatched.front().frame;
            out << "(port=" << read32(frame.data() + 28)
                << ",side=" << unsigned(frame[32])
                << ",process=" << read32(frame.data() + 36) << ')';
        }
    }
    for (unsigned id = 0; id < contexts.size(); ++id) {
        const auto& c = contexts[id];
        if (!c.active) continue;
        out << " ctx" << id << "(pid=" << c.process << ",lane=" << c.lane
            << ",channels=" << c.channels.size() << ",ready=" << c.ready.size()
            << ",pending=" << c.pending.size() << ",status=" << status(c) << ",error=" << c.error
            << ",wait_admission=" << c.waitAdmission << ",notify=" << c.notification << ')';
        out << " ports[";
        for (const auto port : c.channels) out << port << ',';
        out << ']';
    }
    return out.str();
}

uint64_t ChimaeraM5::dispatch(ThreadContext* tc, bool data, uint64_t command, Addr addr, uint64_t size)
{
    for (auto* device : devices) {
        if (device->sys != tc->getSystemPtr()) continue;
        // A KVM CPU may run on a different queue from the PCI function. First
        // serialize device state; no guest ThreadContext is retained afterwards.
        EventQueue::ScopedMigration migration(device->eventQueue());
        uint64_t result;
        try {
            result = device->invoke(tc, data, command, addr, size);
        } catch (const std::exception& e) {
            // Corrupt/failed mailboxes are persistent terminal readiness, not a
            // host exception escaping the simulation handler or a blocked wait.
            warn("ChimaeraM5 request failed: %s", e.what());
            for (unsigned id = 0; id < device->contexts.size(); ++id)
                if (device->contexts[id].active) device->fail(id, EIO);
            result = errorResult(EIO);
        }
        device->updateInterrupt();
        return result;
    }
    return errorResult(ENODEV);
}
uint64_t ChimaeraM5::invoke(ThreadContext* tc, bool data, uint64_t command, Addr addr, uint64_t size)
{
    if (size != sizeof(chm5_request)) return errorResult(EINVAL);
    TranslatingPortProxy caller(tc);
    chm5_request request{};
    if (!caller.tryReadBlob(addr, &request, sizeof(request))) return errorResult(EFAULT);
    if (request.version != CHM5_ABI_VERSION || request.size != sizeof(request))
        return errorResult(EPROTONOSUPPORT);
    auto reply = [&]() -> uint64_t {
        return caller.tryWriteBlob(addr, &request, sizeof(request)) ? 0 : errorResult(EFAULT);
    };
    if (!data && command == CHM5_CTL_QUERY) {
        request.length = CHM5_MAX_BYTES;
        request.slot = CHM5_MAX_SLOTS;
        request.status = CHM5_FEATURE_FUNCTIONAL | CHM5_FEATURE_PHYSICAL_RX |
            CHM5_FEATURE_LANES | CHM5_FEATURE_ADMISSION_IRQ | CHM5_FEATURE_RX_BATCH;
        return reply();
    }
    if (request.context >= contexts.size()) return errorResult(EINVAL);
    auto& c = contexts[request.context];
    if (!c.active || c.generation != request.generation) return errorResult(ESTALE);
    if (c.process != request.process || c.lane != request.lane) return errorResult(EPERM);
    if (c.error) return errorResult(c.error);
    if (c.shutdown && !(!data && command == CHM5_CTL_CLOSE)) return errorResult(ESHUTDOWN);
    if (!data) {
        switch (command) {
          case CHM5_CTL_BIND:
            request.length = c.slotBytes;
            request.slot = c.slots.size();
            break;
          case CHM5_CTL_OPEN_CHANNEL:
            if (bool(request.port & CHM5_BULK_PORT_BIT) != bool(c.lane)) return errorResult(EINVAL);
            if (!c.channels.count(request.port) && c.channels.size() >= CHM5_MAX_CHANNELS)
                return errorResult(ENOSPC);
            c.channels.insert(request.port);
            retryUnmatched(c.lane);
            break;
          case CHM5_CTL_CLOSE_CHANNEL:
            c.channels.erase(request.port);
            break;
          case CHM5_CTL_ARM:
            // Persistent queue level is authoritative, including wake-before-arm.
            request.sequence = c.notification;
            request.status = status(c);
            request.error = c.error;
            break;
          case CHM5_CTL_CLOSE:
            c.shutdown = true;
            c.channels.clear();
            notify(request.context);
            break;
          case CHM5_CTL_REGISTER:
          case CHM5_CTL_UNREGISTER:
            return errorResult(EPERM); // physical lists are kernel-owned
          default: return errorResult(EINVAL);
        }
        return reply();
    }
    switch (command) {
      case CHM5_DATA_SUBMITV: {
        if (request.bytes0 != HeaderBytes || request.bytes1 > CHM5_MAX_BYTES - HeaderBytes)
            return errorResult(EMSGSIZE);
        const auto length = size_t(request.bytes0 + request.bytes1);
        auto bytes = std::make_shared<std::vector<uint8_t>>(length);
        if (!caller.tryReadBlob(request.address0, bytes->data(), HeaderBytes)) return errorResult(EFAULT);
        // Validate header identity and lane before touching a potentially large body.
        if (!validFrame(*bytes, 2) || frameLane(*bytes) != c.lane ||
            read32(bytes->data() + 16) != c.process) return errorResult(EPROTO);
        const auto targets = recipients(*bytes, c.lane);
        for (auto id : targets) {
            if (length > contexts[id].slotBytes) return errorResult(EMSGSIZE);
            if (!canQueue(contexts[id], length)) return errorResult(EAGAIN);
        }
        auto& l = lanes[c.lane];
        const bool remote = (*bytes)[32] != 2;
        if (remote && (l.outgoing.size() >= maxPending || length > maxPendingBytes - l.outgoingBytes))
            return errorResult(EAGAIN);
        if (request.bytes1 && !caller.tryReadBlob(request.address1, bytes->data() + HeaderBytes,
                request.bytes1)) return errorResult(EFAULT);
        const Packet packet{bytes, admissionEpoch, ++packetSequence};
        for (auto id : targets) queue(contexts[id], packet);
        if (remote) {
            l.outgoing.push_back(packet);
            l.outgoingBytes += length;
            // Mailboxes attach only at ingress; before that, startup discovery
            // remains in this bounded host queue. FIFO survives ring pressure.
            flushOutgoing(c.lane);
        }
        return 0; // No fallible guest write after accepting ownership.
      }
      case CHM5_DATA_REAP: {
        if (c.ready.empty()) return errorResult(EAGAIN);
        const auto id = c.ready.front();
        auto& slot = c.slots[id];
        request.slot = id;
        request.length = slot.length;
        request.sequence = c.notification;
        request.admission_epoch = slot.epoch;
        request.packet_sequence = slot.sequence;
        request.status = status(c);
        const auto result = reply();
        if (result) return result;
        c.ready.pop_front();
        slot.state = SlotState::loaned;
        return 0;
      }
      case CHM5_DATA_REAP_BATCH: {
        if (!request.bytes0 || request.bytes0 % sizeof(chm5_completion) ||
            request.bytes0 > CHM5_MAX_SLOTS * sizeof(chm5_completion))
            return errorResult(EINVAL);
        if (c.ready.empty()) return errorResult(EAGAIN);
        const auto count = std::min<size_t>(c.ready.size(),
            request.bytes0 / sizeof(chm5_completion));
        std::vector<chm5_completion> completions;
        completions.reserve(count);
        for (size_t index = 0; index < count; ++index) {
            const auto id = c.ready[index];
            const auto& slot = c.slots[id];
            completions.push_back({id, slot.length, slot.sequence, slot.epoch});
        }
        if (!caller.tryWriteBlob(request.address0, completions.data(),
                count * sizeof(chm5_completion))) return errorResult(EFAULT);
        request.length = count;
        request.sequence = c.notification;
        request.status = status(c);
        if (const auto result = reply()) return result;
        // Both guest writes succeeded before handing out any loan.
        for (size_t index = 0; index < count; ++index) {
            c.slots[c.ready.front()].state = SlotState::loaned;
            c.ready.pop_front();
        }
        return 0;
      }
      case CHM5_DATA_RELEASE_BATCH: {
        if (!request.bytes0 || request.bytes0 % sizeof(chm5_completion) ||
            request.bytes0 > CHM5_MAX_SLOTS * sizeof(chm5_completion))
            return errorResult(EINVAL);
        const auto count = request.bytes0 / sizeof(chm5_completion);
        std::vector<chm5_completion> completions(count);
        if (!caller.tryReadBlob(request.address0, completions.data(), request.bytes0))
            return errorResult(EFAULT);
        std::set<uint32_t> unique;
        for (const auto& completion : completions) {
            if (completion.slot >= c.slots.size() || !unique.insert(completion.slot).second ||
                c.slots[completion.slot].state != SlotState::loaned)
                return errorResult(EINVAL);
            if (completion.packet_sequence != c.slots[completion.slot].sequence)
                return errorResult(ESTALE);
        }
        for (const auto& completion : completions)
            c.slots[completion.slot].state = SlotState::posted;
        fill(c);
        retryUnmatched(c.lane);
        return 0;
      }
      case CHM5_DATA_RELEASE: {
        if (request.slot >= c.slots.size() || c.slots[request.slot].state != SlotState::loaned)
            return errorResult(EINVAL);
        auto& slot = c.slots[request.slot];
        if (request.packet_sequence != slot.sequence) return errorResult(ESTALE);
        slot.state = SlotState::posted;
        fill(c);
        retryUnmatched(c.lane);
        return 0;
      }
      case CHM5_DATA_POST_RX:
        // Kernel registration posts the whole pool once. Subsequent credits
        // return only through RELEASE, which validates the packet identity.
        return errorResult(EALREADY);
      default: return errorResult(EINVAL);
    }
}

Tick ChimaeraM5::readDevice(PacketPtr packet)
{
    int bar;
    Addr offset;
    panic_if(!getBAR(packet->getAddr(), bar, offset) || bar, "Invalid ChimaeraM5 BAR");
    uint64_t value = 0;
    if (offset == CHM5_REG_MAGIC && packet->getSize() == 4) value = CHM5_MAGIC;
    else if (offset == CHM5_REG_VERSION && packet->getSize() == 4) value = CHM5_ABI_VERSION;
    else if (offset == CHM5_REG_IRQ && packet->getSize() == 8) value = irqPending;
    else if (offset == CHM5_REG_ADMISSION_EPOCH && packet->getSize() == 8) value = admissionEpoch;
    else if (offset == CHM5_REG_FEATURES && packet->getSize() == 4)
        value = CHM5_FEATURE_FUNCTIONAL | CHM5_FEATURE_PHYSICAL_RX |
            CHM5_FEATURE_LANES | CHM5_FEATURE_ADMISSION_IRQ | CHM5_FEATURE_RX_BATCH;
    else if (offset >= CHM5_CONTEXT_BASE) {
        const auto id = (offset - CHM5_CONTEXT_BASE) / CHM5_CONTEXT_STRIDE;
        const auto reg = (offset - CHM5_CONTEXT_BASE) % CHM5_CONTEXT_STRIDE;
        if (id < contexts.size()) {
            const auto& c = contexts[id];
            if (reg == CHM5_CTX_GENERATION && packet->getSize() == 8) value = c.generation;
            else if (reg == CHM5_CTX_SEQUENCE && packet->getSize() == 8) value = c.notification;
            else if (reg == CHM5_CTX_STATUS && packet->getSize() == 4) value = status(c);
            else if (reg == CHM5_CTX_ERROR && packet->getSize() == 4) value = c.error;
        }
    }
    if (packet->getSize() == 8) packet->setLE<uint64_t>(value);
    else if (packet->getSize() == 4) packet->setLE<uint32_t>(value);
    else std::memset(packet->getPtr<uint8_t>(), 0, packet->getSize());
    packet->makeAtomicResponse();
    return pioDelay;
}
Tick ChimaeraM5::writeDevice(PacketPtr packet)
{
    int bar;
    Addr offset;
    panic_if(!getBAR(packet->getAddr(), bar, offset) || bar, "Invalid ChimaeraM5 BAR");
    if (offset == CHM5_REG_REGISTER && packet->getSize() == 8) registerContext(packet->getLE<uint64_t>());
    else if (offset == CHM5_REG_ACK_IRQ && packet->getSize() == 8) irqPending &= ~packet->getLE<uint64_t>();
    else if (offset >= CHM5_CONTEXT_BASE && packet->getSize() == 4) {
        const auto id = (offset - CHM5_CONTEXT_BASE) / CHM5_CONTEXT_STRIDE;
        const auto reg = (offset - CHM5_CONTEXT_BASE) % CHM5_CONTEXT_STRIDE;
        if (reg == CHM5_CTX_CLOSE) closeContext(id);
        else if (id < contexts.size() && reg == CHM5_CTX_ARM_ADMISSION && contexts[id].active) {
            contexts[id].waitAdmission = packet->getLE<uint32_t>() != 0;
            if (contexts[id].waitAdmission && admissionEpoch) notify(id);
        }
    }
    packet->makeAtomicResponse();
    updateInterrupt();
    return pioDelay;
}
Tick ChimaeraM5::writeConfig(PacketPtr packet)
{
    const auto delay = PciEndpoint::writeConfig(packet);
    updateInterrupt();
    return delay;
}
void ChimaeraM5::serialize(CheckpointOut&) const
{
    fatal("ChimaeraM5 requires fresh boot and rebind; checkpoints are unsupported");
}
void ChimaeraM5::unserialize(CheckpointIn&)
{
    fatal("ChimaeraM5 requires fresh boot and rebind; checkpoint restore is unsupported");
}
} } // namespace gem5::chimaera
