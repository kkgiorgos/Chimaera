/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_DMA_DEVICE_HH
#define GEM5_CHIMAERA_DMA_DEVICE_HH

#include <deque>
#include <memory>
#include <vector>

#include "dev/pci/device.hh"
#include "gem5/chimaera_dma.h"
#include "gem5/chimaera_mailbox.hh"
#include "sim/eventq.hh"

namespace gem5 {
struct ChimaeraDmaParams;
namespace chimaera {

class ChimaeraDma : public PciEndpoint
{
    struct Transfer {
        bool active{};
        unsigned stage{};
        Addr descriptorAddress{};
        chdma_descriptor descriptor{};
        std::vector<chdma_segment> segments;
        std::uint8_t* data{};
        std::size_t length{}, pending{};
    };
    const bool loopback;
    const std::string h2gPath, g2hPath;
    const unsigned maxPending;
    const unsigned mailboxSlots;
    std::unique_ptr<chimaera_mailbox::Mailbox> h2g, g2h;
    std::deque<Addr> postedRx;
    std::deque<std::vector<std::uint8_t>> echoed;
    std::vector<std::uint8_t> echoTx;
    Transfer tx, rx;
    std::uint32_t interrupts{}, error{}, rxLength{};
    std::uint64_t rxCookie{}, txCookie{};
    bool irqAsserted{}, resetting{};
    EventFunctionWrapper txHeaderDone, rxHeaderDone, txSegmentsDone, rxSegmentsDone;

    void updateInterrupt();
    void fail(std::uint32_t code);
    void tryTx();
    void tryRx();
    void headerDone(bool transmit);
    void segmentsDone(bool transmit);
    void payloadDone(bool transmit);
    void reset();
    void finishReset();
    bool validMemory(Addr address, std::size_t length) const;
    Tick readDevice(PacketPtr packet) override;
    Tick writeDevice(PacketPtr packet) override;
    Tick writeConfig(PacketPtr packet) override;

public:
    using Params = ChimaeraDmaParams;
    explicit ChimaeraDma(const Params& params);
    void ingress(); // Called only while the timing server has gem5 paused.
    void serialize(CheckpointOut& cp) const override;
    void unserialize(CheckpointIn& cp) override;
};
} // namespace chimaera
} // namespace gem5
#endif
