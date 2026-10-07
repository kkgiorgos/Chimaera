// SPDX-License-Identifier: MIT
// Adapted from CMU-SAFARI/ramulator2; see LICENSE and
// ext/ramulator2/README.md.
#include "mem/ramulator2/ramulator2_base.hh"

#include <cmath>
#include <fstream>

#include "base/callback.hh"
#include "base/output.hh"
#include "base/trace.hh"
#include "debug/Ramulator2.hh"
#include "sim/system.hh"

// gem5's warn macro conflicts with Ramulator's logger.
#pragma push_macro("warn")
#undef warn
#include "ramulator/base/base.h"
#include "ramulator/base/config.h"
#include "ramulator/base/request.h"
#include "ramulator/frontend/i_frontend.h"
#include "ramulator/memory_system/i_memory_system.h"
#pragma pop_macro("warn")

namespace gem5
{
namespace memory
{

Ramulator2Base::Ramulator2Base(const AbstractMemoryParams &p,
                               const std::string &config, size_t num_ports,
                               unsigned int max_outstanding)
    : AbstractMemory(p),
      ramulator_config(config),
      ramulator2_frontend(nullptr),
      ramulator2_memorysystem(nullptr),
      ramulator2_finalized(false),
      clockPeriod(0),
      maxOutstanding(max_outstanding),
      nbrOutstandingReads(0),
      nbrOutstandingWrites(0),
      contentionStats(this),
      tickEvent([this] { tick(); }, name())
{
    fatal_if(maxOutstanding < 2,
             "Ramulator2 max_outstanding must be at least two");
    for (size_t i = 0; i < num_ports; ++i) {
        portStates.push_back(std::make_unique<PortState>(*this, i));
    }
    registerExitCallback([this]() {
        writeRamulatorStats(simout.resolve(name() + ".ramulator_stats.yaml"),
                            StatsWriteMode::Final);
    });
}

Ramulator2Base::ContentionStats::ContentionStats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(aggressorAccepted, statistics::units::Count::get(), "Accepted synthetic requests"),
      ADD_STAT(aggressorRejected, statistics::units::Count::get(), "Offers blocked by queue capacity"),
      ADD_STAT(aggressorBytes, statistics::units::Byte::get(), "Accepted synthetic traffic bytes"),
      ADD_STAT(aggressorCompleted, statistics::units::Count::get(), "Completed synthetic requests"),
      ADD_STAT(aggressorLatencyTicks, statistics::units::Tick::get(), "Synthetic completion latency sum"),
      ADD_STAT(cpuReads, statistics::units::Count::get(), "Completed CPU-side DRAM reads"),
      ADD_STAT(cpuReadLatencyTicks, statistics::units::Tick::get(), "CPU-side DRAM read latency sum"),
      ADD_STAT(cpuRetries, statistics::units::Count::get(), "CPU-side rejected submissions")
{}

void
Ramulator2Base::injectAggressor()
{
    // An independent device ingress: no gem5 packets, cache pollution, CPU
    // instructions or backing-store writes. Addresses are above guest RAM.
    const uint64_t cycle = aggressorCycle++;
    if (aggressorPattern == "none" || drainState() != DrainState::Running ||
        cycle % aggressorInterval ||
        cycle % aggressorPeriod >=
            uint64_t(aggressorPeriod) * aggressorDutyPercent / 100) {
        return;
    }
    if (aggressorPending >= aggressorMaxPending) {
        ++contentionStats.aggressorRejected;
        return;
    }
    const unsigned int bytes = ramulator2_memorysystem->get_tx_bytes();
    fatal_if(aggressorWindow % bytes || aggressorStride % bytes,
             "Aggressor window and stride must align to DRAM transactions");
    Addr offset;
    if (aggressorPattern == "random") {
        aggressorState ^= aggressorState << 13;
        aggressorState ^= aggressorState >> 7;
        aggressorState ^= aggressorState << 17;
        offset = (aggressorState % (aggressorWindow / bytes)) * bytes;
    } else {
        offset = (aggressorIndex * (aggressorPattern == "stream" ? bytes :
                                  aggressorStride)) % aggressorWindow;
    }
    const Addr address = getAddrRange().size() + offset;
    const bool read = aggressorIndex % 100 < aggressorReadPercent;
    const Tick submitted = curTick();
    ++aggressorPending; // Writes may complete synchronously.
    const bool accepted = ramulator2_frontend->receive_external_requests(
        // External advertises one source to controller statistics. Distinguish
        // guest/device ownership in our callbacks and counters, not source_id.
        read ? 0 : 1, address, 0, -1,
        [this, submitted](Ramulator::Request &) {
            --aggressorPending;
            ++contentionStats.aggressorCompleted;
            contentionStats.aggressorLatencyTicks += curTick() - submitted;
            if (!nbrOutstanding()) {
                signalDrainDone();
            }
        }, bytes);
    if (accepted) {
        ++aggressorIndex;
        ++contentionStats.aggressorAccepted;
        contentionStats.aggressorBytes += bytes;
    } else {
        --aggressorPending;
        ++contentionStats.aggressorRejected;
    }
}

Ramulator2Base::~Ramulator2Base()
{
    delete ramulator2_frontend;
    delete ramulator2_memorysystem;
}

void
Ramulator2Base::initRamulator()
{
    try {
        auto config = Ramulator::Config::parse_config_string(ramulator_config);
        ramulator2_frontend = Ramulator::Factory::create_frontend(config);
        ramulator2_memorysystem =
            Ramulator::Factory::create_memory_system(config);
        ramulator2_frontend->connect_memory_system(ramulator2_memorysystem);
        ramulator2_memorysystem->connect_frontend(ramulator2_frontend);
    } catch (const std::exception &error) {
        fatal("Ramulator2 configuration failed: %s", error.what());
    }
    const double period =
        ramulator2_memorysystem->get_tCK() * sim_clock::as_float::ns;
    fatal_if(!std::isfinite(period) || period < 1,
             "Ramulator2 tCK must be at least one gem5 tick");
    clockPeriod = std::llround(period);
    fatal_if(system()->cacheLineSize() >
                 ramulator2_memorysystem->get_tx_bytes(),
             "Ramulator2 DRAM transaction is smaller than the cache line; "
             "request splitting is unsupported");
}

void
Ramulator2Base::startup()
{
    // Atomic/KVM boot accesses use gem5's backing store. Do not schedule
    // billions of unused DRAM ticks during boot; CPU switching resumes us.
    drainResume();
}

void
Ramulator2Base::drainResume()
{
    if (system()->isTimingMode() && !tickEvent.scheduled()) {
        schedule(tickEvent, curTick());
    }
}

void
Ramulator2Base::resetStats()
{
    AbstractMemory::resetStats();
    if (ramulator2_frontend) {
        ramulator2_frontend->reset_stats_recursive();
    }
    if (ramulator2_memorysystem) {
        ramulator2_memorysystem->reset_stats_recursive();
    }
}

void
Ramulator2Base::preDumpStats()
{
    AbstractMemory::preDumpStats();
    writeRamulatorStats(simout.resolve(name() + ".ramulator_stats." +
                                       std::to_string(curTick()) + ".yaml"),
                        StatsWriteMode::Snapshot);
}

void
Ramulator2Base::writeRamulatorStats(const std::string &path,
                                    StatsWriteMode mode)
{
    if (!ramulator2_frontend || !ramulator2_memorysystem) {
        return;
    }
    if (mode == StatsWriteMode::Final) {
        if (!ramulator2_finalized) {
            ramulator2_frontend->finalize();
            ramulator2_memorysystem->finalize();
            ramulator2_finalized = true;
        }
    } else {
        ramulator2_frontend->update_stats_recursive();
        ramulator2_memorysystem->update_stats_recursive();
    }
    std::ofstream output(path);
    fatal_if(!output, "Cannot open Ramulator2 statistics file %s", path);
    ramulator2_frontend->print_stats(output);
    ramulator2_memorysystem->print_stats(output);
    output.flush();
    fatal_if(!output, "Cannot write Ramulator2 statistics file %s", path);
}

unsigned int
Ramulator2Base::nbrOutstanding() const
{
    unsigned int count =
        nbrOutstandingReads + nbrOutstandingWrites + submittingRequest +
        aggressorPending;
    for (const auto &state : portStates) {
        count += state->responseQueue.size();
    }
    return count;
}

void
Ramulator2Base::sendResponse(PortID port_id)
{
    auto &state = *portStates.at(port_id);
    assert(!state.retryResp && !state.responseQueue.empty());
    if (state.responseQueue.front().ready > curTick()) {
        schedule(state.sendResponseEvent, state.responseQueue.front().ready);
        return;
    }
    if (getMemoryPort(port_id).sendTimingResp(
            state.responseQueue.front().packet)) {
        state.responseQueue.pop_front();
        if (!state.responseQueue.empty() &&
            !state.sendResponseEvent.scheduled()) {
            schedule(state.sendResponseEvent,
                     std::max(curTick(), state.responseQueue.front().ready));
        }
        if (!nbrOutstanding()) {
            signalDrainDone();
        }
    } else {
        state.retryResp = true;
    }
}

void
Ramulator2Base::tick()
{
    if (!system()->isTimingMode()) {
        return;
    }
    processingTick = true;
    ramulator2_memorysystem->tick();
    injectAggressor();
    for (size_t i = 0; i < portStates.size(); ++i) {
        auto &state = *portStates[i];
        if (state.retryReq && nbrOutstanding() - aggressorPending < maxOutstanding) {
            state.retryReq = false;
            getMemoryPort(i).sendRetryReq();
        }
    }
    processingTick = false;
    // Draining caches can send requests after this memory first reported
    // Drained. Continue servicing them until the whole system is quiescent.
    if ((drainState() != DrainState::Drained || nbrOutstanding()) &&
        !tickEvent.scheduled()) {
        schedule(tickEvent, curTick() + clockPeriod);
    }
}

Tick
Ramulator2Base::recvAtomic(PacketPtr pkt)
{
    access(pkt);
    return pkt->cacheResponding() ? 0 : 50 * sim_clock::as_int::ns;
}

void
Ramulator2Base::recvFunctional(PacketPtr pkt)
{
    pkt->pushLabel(name());
    functionalAccess(pkt);
    for (auto &state : portStates) {
        for (const auto &response : state->responseQueue) {
            pkt->trySatisfyFunctional(response.packet);
        }
    }
    pkt->popLabel();
}

bool
Ramulator2Base::recvTimingReq(PacketPtr pkt, PortID port_id)
{
    auto &state = *portStates.at(port_id);
    if (pkt->cacheResponding()) {
        state.pendingDelete.reset(pkt);
        return true;
    }
    if (state.retryReq) {
        return false;
    }
    // A write occupies a backend slot and, until acknowledged, a response
    // slot. Reserve both so downstream backpressure cannot exceed the cap.
    const unsigned int slots = pkt->isWrite() && pkt->needsResponse() ? 2 : 1;
    if (nbrOutstanding() - aggressorPending + slots > maxOutstanding) {
        ++contentionStats.cpuRetries;
        state.retryReq = true;
        return false;
    }
    if (!(pkt->isRead() || pkt->isWrite())) {
        accessAndRespond(pkt, port_id);
        return true;
    }

    const Addr address = pkt->getAddr() - getAddrRange().start();
    const unsigned int transaction = ramulator2_memorysystem->get_tx_bytes();
    fatal_if(address % transaction + pkt->getSize() > transaction,
             "Ramulator2 packet crosses a DRAM transaction boundary");
    DPRINTF(Ramulator2, "Request %s addr %#x size %d port %d\n",
            pkt->cmdString(), pkt->getAddr(), pkt->getSize(), port_id);

    // Record ownership BEFORE submission: coalesced writes can invoke their
    // callback synchronously. Capture each read packet, not an address FIFO,
    // since equal-address requests on different ports may reorder.
    submittingRequest = true;
    const bool read = pkt->isRead();
    if (read) {
        outstandingReads.insert(pkt);
        ++nbrOutstandingReads;
    } else {
        ++nbrOutstandingWrites;
    }
    const Tick submitted = curTick();
    auto callback = [this, pkt, port_id, read, submitted](Ramulator::Request &req) {
        if (read) {
            const auto erased = outstandingReads.erase(pkt);
            panic_if(erased != 1, "Unknown Ramulator2 read completion");
            --nbrOutstandingReads;
            ++contentionStats.cpuReads;
            contentionStats.cpuReadLatencyTicks += curTick() - submitted;
            accessAndRespond(pkt, port_id);
        } else {
            --nbrOutstandingWrites;
        }
        if (!nbrOutstanding()) {
            signalDrainDone();
        }
    };
    const bool accepted = ramulator2_frontend->receive_external_requests(
        read ? 0 : 1, address, 0, getIngressId(port_id), callback,
        pkt->getSize());
    if (!accepted) {
        ++contentionStats.cpuRetries;
        if (read) {
            outstandingReads.erase(pkt);
            --nbrOutstandingReads;
        } else {
            --nbrOutstandingWrites;
        }
        state.retryReq = true;
    } else if (!read) {
        // Store data and acknowledge writes immediately, just like MemCtrl.
        accessAndRespond(pkt, port_id);
    }
    submittingRequest = false;
    if (!nbrOutstanding()) {
        signalDrainDone();
    }
    if (accepted && !processingTick && !tickEvent.scheduled()) {
        schedule(tickEvent, curTick());
    }
    return accepted;
}

void
Ramulator2Base::recvRespRetry(PortID port_id)
{
    auto &state = *portStates.at(port_id);
    assert(state.retryResp);
    state.retryResp = false;
    sendResponse(port_id);
}

void
Ramulator2Base::accessAndRespond(PacketPtr pkt, PortID port_id)
{
    const bool needs_response = pkt->needsResponse();
    access(pkt);
    auto &state = *portStates.at(port_id);
    if (needs_response) {
        assert(pkt->isResponse());
        const Tick ready = curTick() + pkt->headerDelay + pkt->payloadDelay;
        pkt->headerDelay = pkt->payloadDelay = 0;
        state.responseQueue.push_back({ready, pkt});
        if (!state.retryResp && !state.sendResponseEvent.scheduled()) {
            schedule(state.sendResponseEvent,
                     std::max(curTick(), state.responseQueue.front().ready));
        }
    } else {
        state.pendingDelete.reset(pkt);
    }
}

DrainState
Ramulator2Base::drain()
{
    if (nbrOutstanding()) {
        return DrainState::Draining;
    }
    if (tickEvent.scheduled()) {
        deschedule(tickEvent);
    }
    return DrainState::Drained;
}

Ramulator2Base::MemorySystemPort::MemorySystemPort(const std::string &name,
                                                   Ramulator2Base &memory,
                                                   PortID port_id)
    : ResponsePort(name), ramulator2(memory), portId(port_id)
{}

AddrRangeList
Ramulator2Base::MemorySystemPort::getAddrRanges() const
{
    return {ramulator2.getPortRange(portId)};
}

Ramulator2Base::PortState::PortState(Ramulator2Base &memory, PortID port_id)
    : sendResponseEvent([&memory, port_id] { memory.sendResponse(port_id); },
                        memory.name() + ".sendResponse" +
                            std::to_string(port_id))
{}

} // namespace memory
} // namespace gem5
