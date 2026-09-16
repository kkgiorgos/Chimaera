#pragma once

#include <chimaera/transport.hpp>

namespace chimaera {

// The application owns the libm5 address mapping and must keep it mapped for
// every operation. Use only inside an x86 gem5 guest, with calls serialized.
class GuestTransport final : public Transport {
public:
    [[nodiscard]] SendResult send(std::span<const std::byte> data) override;
    [[nodiscard]] ReceiveResult receive() override;

private:
    SendResult failure_;
};

} // namespace chimaera
