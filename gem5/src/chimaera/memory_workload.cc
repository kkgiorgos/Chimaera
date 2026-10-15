// SPDX-License-Identifier: MIT
#include "chimaera/memory_workload.hh"
#include <unordered_map>
#include "arch/generic/isa.hh"
#include "base/logging.hh"
#include "cpu/thread_context.hh"
#include "sim/full_system.hh"
#if CHIMAERA_X86_ATTRIBUTION
#include "arch/x86/isa.hh"
#include "arch/x86/regs/misc.hh"
#endif
namespace gem5::chimaera
{
namespace
{
// This diagnostic registry follows an address space across cores and threads.
// Full-system checkpoints with active attribution are deliberately
// unsupported.
std::unordered_map<const System *, std::unordered_map<Addr, int>> roles;
Addr
addressSpace(ThreadContext *tc)
{
#if CHIMAERA_X86_ATTRIBUTION
    if (dynamic_cast<X86ISA::ISA *>(tc->getIsaPtr())) {
        return tc->readMiscRegNoEffect(X86ISA::misc_reg::Cr3) & ~Addr(0xfff);
    }
#endif
    return 0;
}
} // namespace
void
registerMemoryWorkload(ThreadContext *tc, uint64_t role)
{
    fatal_if(!FullSystem || role < 1 || role > 4 || !addressSpace(tc),
             "ROS DRAM attribution requires x86 full-system and role 1..4");
    auto &map = roles[tc->getSystemPtr()];
    const auto asid = addressSpace(tc);
    if (map.count(asid) && map.at(asid) == role) {
        return;
    }
    map[asid] = role;
    inform("ROS_DRAM_REGISTER role=%s address_space=%#x\n",
           DramNodeNames[role], asid);
}
void
tagMemoryRequest(const RequestPtr &req, ThreadContext *tc)
{
    const auto registry = roles.find(tc->getSystemPtr());
    if (!FullSystem || registry == roles.end()) {
        return;
    }
    int role =
        5; // Kernel instructions/loads stay separate, including syscalls.
    if (tc->getIsaPtr()->inUserMode()) {
        const auto found = registry->second.find(addressSpace(tc));
        role = found == registry->second.end() ? 6 : found->second;
    }
    req->setExtension(std::make_shared<MemoryWorkloadTag>(role));
}
} // namespace gem5::chimaera
