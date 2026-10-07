// SPDX-License-Identifier: MIT
// Adapted from CMU-SAFARI/ramulator2; see LICENSE.
#ifndef __MEM_RAMULATOR2_BASE_HH__
#define __MEM_RAMULATOR2_BASE_HH__

#include <deque>
#include <memory>
#include <string>
#include <unordered_set>
#include <vector>

#include "mem/abstract_mem.hh"
#include "base/statistics.hh"
#include "params/AbstractMemory.hh"

namespace Ramulator
{
class IFrontEnd;
class IMemorySystem;
} // namespace Ramulator

namespace gem5
{

namespace memory
{

class Ramulator2Base : public AbstractMemory
{
  protected:
    class MemorySystemPort : public ResponsePort
    {
      private:
        Ramulator2Base &ramulator2;
        PortID portId;

      public:
        MemorySystemPort(const std::string &_name, Ramulator2Base &_ramulator2,
                         PortID _port_id);

      protected:
        Tick
        recvAtomic(PacketPtr pkt) override
        {
            return ramulator2.recvAtomic(pkt);
        };
        Tick
        recvAtomicBackdoor(PacketPtr pkt, MemBackdoorPtr &backdoor) override
        {
            Tick latency = ramulator2.recvAtomic(pkt);
            ramulator2.getBackdoor(backdoor);
            return latency;
        }
        void
        recvMemBackdoorReq(const MemBackdoorReq &req,
                           MemBackdoorPtr &backdoor) override
        {
            ramulator2.getBackdoor(backdoor);
        }
        void
        recvFunctional(PacketPtr pkt) override
        {
            ramulator2.recvFunctional(pkt);
        };
        bool
        recvTimingReq(PacketPtr pkt) override
        {
            return ramulator2.recvTimingReq(pkt, portId);
        };
        void
        recvRespRetry() override
        {
            ramulator2.recvRespRetry(portId);
        };

        AddrRangeList getAddrRanges() const override;
    };

    struct Response
    {
        Tick ready;
        PacketPtr packet;
    };

    struct PortState
    {
        bool retryReq = false;
        bool retryResp = false;
        std::deque<Response> responseQueue;
        EventFunctionWrapper sendResponseEvent;
        std::unique_ptr<Packet> pendingDelete;

        PortState(Ramulator2Base &ramulator2, PortID port_id);
    };

    std::vector<std::unique_ptr<PortState>> portStates;

    std::string ramulator_config;
    Ramulator::IFrontEnd *ramulator2_frontend;
    Ramulator::IMemorySystem *ramulator2_memorysystem;
    bool ramulator2_finalized;

    Tick clockPeriod;
    unsigned int maxOutstanding;
    bool submittingRequest = false;
    bool processingTick = false;
    std::unordered_set<PacketPtr> outstandingReads;

    unsigned int nbrOutstandingReads;
    unsigned int nbrOutstandingWrites;

    struct ContentionStats : public statistics::Group
    {
        statistics::Scalar aggressorAccepted, aggressorRejected, aggressorBytes;
        statistics::Scalar aggressorCompleted, aggressorLatencyTicks;
        statistics::Scalar cpuReads, cpuReadLatencyTicks, cpuRetries;
        ContentionStats(statistics::Group *parent);
    } contentionStats;
    std::string aggressorPattern = "none";
    unsigned int aggressorInterval = 4, aggressorStride = 4096;
    unsigned int aggressorReadPercent = 80, aggressorMaxPending = 128;
    unsigned int aggressorDutyPercent = 100, aggressorPeriod = 12000;
    Addr aggressorWindow = 268435456;
    uint64_t aggressorState = 1, aggressorIndex = 0, aggressorCycle = 0;
    unsigned int aggressorPending = 0;
    void injectAggressor();

    Ramulator2Base(const AbstractMemoryParams &p,
                   const std::string &ramulator_config, size_t num_ports,
                   unsigned int max_outstanding);
    ~Ramulator2Base();

    void initRamulator();
    unsigned int nbrOutstanding() const;

    virtual MemorySystemPort &getMemoryPort(PortID port_id) = 0;
    virtual AddrRange getPortRange(PortID port_id) const = 0;
    virtual int getIngressId(PortID port_id) const = 0;

    void accessAndRespond(PacketPtr pkt, PortID port_id);
    void sendResponse(PortID port_id);

    enum class StatsWriteMode
    {
        Snapshot,
        Final
    };
    void writeRamulatorStats(const std::string &path, StatsWriteMode mode);

    void tick();
    EventFunctionWrapper tickEvent;

  public:
    DrainState drain() override;

    void startup() override;
    void drainResume() override;
    void resetStats() override;
    void preDumpStats() override;

  protected:
    Tick recvAtomic(PacketPtr pkt);
    void recvFunctional(PacketPtr pkt);
    bool recvTimingReq(PacketPtr pkt, PortID port_id);
    void recvRespRetry(PortID port_id);
};

} // namespace memory
} // namespace gem5

#endif // __MEM_RAMULATOR2_BASE_HH__
