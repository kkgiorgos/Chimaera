/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_M5_DEVICE_HH
#define GEM5_CHIMAERA_M5_DEVICE_HH

#include <array>
#include <deque>
#include <memory>
#include <set>
#include <vector>

#include "dev/pci/device.hh"
#include "gem5/chimaera_m5.h"
#include "gem5/chimaera_mailbox.hh"
#include "mem/port_proxy.hh"

namespace gem5 {
class ThreadContext;
struct ChimaeraM5Params;
namespace chimaera {

// Payload access is functional/coherent through the system port. The PCI
// function supplies only registration and persistent Linux IRQ notifications.
class ChimaeraM5 : public PciEndpoint
{
    using Frame = std::shared_ptr<const std::vector<uint8_t>>;
    struct Packet {
        Frame frame;
        uint64_t epoch{}, sequence{};
    };
    enum class SlotState { posted, ready, loaned };
    struct Slot {
        std::vector<Addr> pages;
        SlotState state{SlotState::posted};
        uint32_t length{};
        uint64_t epoch{}, sequence{};
    };
    struct Context {
        uint64_t generation{}, notification{};
        uint32_t process{}, lane{}, slotBytes{}, error{};
        bool active{}, shutdown{}, waitAdmission{};
        std::set<uint32_t> channels;
        std::vector<Slot> slots;
        std::deque<uint32_t> ready;
        std::deque<Packet> pending;
        size_t queuedBytes{};
    };
    struct Lane {
        std::unique_ptr<chimaera_mailbox::Mailbox> h2g, g2h;
        std::deque<Packet> outgoing, unmatched;
        size_t outgoingBytes{}, unmatchedBytes{};
    };
    static std::vector<ChimaeraM5*> devices;
    const std::string h2gPath, g2hPath;
    const unsigned mailboxSlots, maxPending, ingressRecords;
    const size_t maxPendingBytes;
    PortProxy physical;
    std::array<Context, CHM5_MAX_CONTEXTS> contexts;
    std::array<Lane, 2> lanes;
    uint64_t irqPending{}, nextGeneration{}, admissionEpoch{}, packetSequence{};
    bool irqAsserted{};

    bool validMemory(Addr, size_t) const;
    void updateInterrupt();
    void notify(unsigned);
    void fail(unsigned, unsigned);
    uint32_t status(const Context&) const;
    void registerContext(Addr);
    void closeContext(unsigned);
    void fill(Context&);
    bool canQueue(const Context&, size_t) const;
    void queue(Context&, const Packet&);
    std::vector<unsigned> recipients(const std::vector<uint8_t>&, unsigned) const;
    bool deliver(const Packet&, unsigned, bool);
    void retryUnmatched(unsigned);
    void connectMailboxes(unsigned);
    void flushOutgoing(unsigned);
    uint64_t invoke(ThreadContext*, bool, uint64_t, Addr, uint64_t);
    Tick readDevice(PacketPtr) override;
    Tick writeDevice(PacketPtr) override;
    Tick writeConfig(PacketPtr) override;
public:
    using Params = ChimaeraM5Params;
    explicit ChimaeraM5(const Params&);
    ~ChimaeraM5() override;
    // Host must call this on the simulation owner thread with all CPUs paused.
    void ingress();
    // Paused-only export: no host input consumption or admission epoch changes.
    uint64_t flush_egress();
    std::string diagnostics();
    static uint64_t dispatch(ThreadContext*, bool, uint64_t, Addr, uint64_t);
    void serialize(CheckpointOut&) const override;
    void unserialize(CheckpointIn&) override;
};
} // namespace chimaera
} // namespace gem5
#endif
