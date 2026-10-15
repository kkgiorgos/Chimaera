// SPDX-License-Identifier: MIT
#ifndef __CHIMAERA_MEMORY_WORKLOAD_HH__
#define __CHIMAERA_MEMORY_WORKLOAD_HH__
#include <array>
#include "mem/request.hh"
namespace gem5
{
class ThreadContext;
namespace chimaera
{
inline constexpr uint64_t DramNodeChannel = 0x524f534452414dULL;
inline constexpr std::array<const char *, 7> DramNodeNames = {
    "unattributed", "perception", "planning",  "control",
    "guest_bridge", "kernel",     "other_user"};
struct MemoryWorkloadTag : public Extension<Request, MemoryWorkloadTag>
{
    int role;
    explicit MemoryWorkloadTag(int value) : role(value) {}
    std::unique_ptr<ExtensionBase>
    clone() const override
    {
        return std::make_unique<MemoryWorkloadTag>(*this);
    }
};
// Belongs to the logical Request, not a Packet allocation: Ruby may rebuild a
// refused Packet while retaining this Request until admission succeeds.
struct DramReadAttempt : public Extension<Request, DramReadAttempt>
{
    Tick first;
    explicit DramReadAttempt(Tick value) : first(value) {}
    std::unique_ptr<ExtensionBase>
    clone() const override
    {
        return std::make_unique<DramReadAttempt>(*this);
    }
};
void registerMemoryWorkload(ThreadContext *tc, uint64_t role);
void tagMemoryRequest(const RequestPtr &req, ThreadContext *tc);
inline int
memoryWorkload(const RequestPtr &req)
{
    const auto tag = req->getExtension<MemoryWorkloadTag>();
    return tag ? tag->role : 0;
}
} // namespace chimaera
} // namespace gem5
#endif
