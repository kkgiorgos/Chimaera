#pragma once

#include <chimaera/transport.hpp>

namespace chimaera {

// Address operations support KVM and require an application-owned libm5 mapping.
// Instruction operations support simulated CPUs (TimingSimpleCPU/O3), require no
// mapping or /dev/mem access, and must never execute under KVM or on the host.
enum class GuestM5Ops { address, instruction };

// Use only inside an x86 gem5 guest, with calls serialized.
class GuestTransport final : public Transport {
public:
    explicit GuestTransport(GuestM5Ops ops = GuestM5Ops::address) : ops_(ops) {}
    [[nodiscard]] SendResult send(std::span<const std::byte> data) override;
    [[nodiscard]] ReceiveResult receive() override;

private:
    GuestM5Ops ops_;
    SendResult failure_;
};

} // namespace chimaera
