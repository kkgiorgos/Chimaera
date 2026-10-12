// SPDX-License-Identifier: MIT
// Adapted from CMU-SAFARI/ramulator2; see LICENSE.
#include "mem/ramulator2/ramulator2.hh"

#include "base/trace.hh"
#include "debug/Ramulator2.hh"

namespace gem5
{

namespace memory
{

Ramulator2::Ramulator2(const Params &p)
    : Ramulator2Base(p, p.ramulator_config, 1, p.max_outstanding),
      port(name() + ".port", *this, 0)
{
    aggressorPattern = p.aggressor_pattern;
    aggressorInterval = p.aggressor_interval;
    aggressorStride = p.aggressor_stride;
    aggressorReadPercent = p.aggressor_read_percent;
    aggressorMaxPending = p.aggressor_max_pending;
    aggressorWindow = p.aggressor_window;
    aggressorState = p.aggressor_seed;
    aggressorDutyPercent = p.aggressor_duty_percent;
    aggressorPeriod = p.aggressor_period;
    fatal_if(aggressorPattern != "none" && aggressorPattern != "stream" &&
             aggressorPattern != "stride" && aggressorPattern != "random",
             "Unknown Ramulator aggressor pattern");
    fatal_if(!aggressorInterval || !aggressorMaxPending || !aggressorPeriod ||
             !aggressorState || aggressorReadPercent > 100 ||
             aggressorDutyPercent > 100 || aggressorWindow < 64 ||
             aggressorWindow % 64 || !aggressorStride || aggressorStride % 64,
             "Invalid Ramulator aggressor configuration");

    DPRINTF(Ramulator2, "Instantiated single-port Ramulator2\n");
}

void
Ramulator2::init()
{
    AbstractMemory::init();

    if (!port.isConnected()) {
        fatal("Ramulator2 %s is unconnected!\n", name());
    }

    port.sendRangeChange();
    initRamulator();
}

Ramulator2::MemorySystemPort &
Ramulator2::getMemoryPort(PortID port_id)
{
    assert(port_id == 0);
    return port;
}

AddrRange
Ramulator2::getPortRange(PortID port_id) const
{
    assert(port_id == 0);
    return getAddrRange();
}

int
Ramulator2::getIngressId(PortID port_id) const
{
    assert(port_id == 0);
    return -1;
}

Port &
Ramulator2::getPort(const std::string &if_name, PortID idx)
{
    if (if_name == "port") {
        return port;
    }
    return ClockedObject::getPort(if_name, idx);
}

} // namespace memory
} // namespace gem5
