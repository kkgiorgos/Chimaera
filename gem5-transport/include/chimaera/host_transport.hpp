#pragma once

#include <chimaera/transport.hpp>
#include <memory>

namespace chimaera {

// Construct before the guest starts transferring: both listeners are opened
// immediately. Paths must match gem5/src/chimaera/util.hh.
class HostTransport final : public Transport {
public:
    explicit HostTransport(
        std::string guest_to_host = "/tmp/chimaera_g2h.sock",
        std::string host_to_guest = "/tmp/chimaera_h2g.sock");
    ~HostTransport() override;
    HostTransport(const HostTransport&) = delete;
    HostTransport& operator=(const HostTransport&) = delete;

    [[nodiscard]] SendResult send(std::span<const std::byte> data) override;
    [[nodiscard]] ReceiveResult receive() override;

    // The only operation permitted from another thread. Permanently wakes
    // blocked socket I/O; join that thread before destroying this instance.
    void cancel() noexcept;

private:
    class Implementation;
    std::unique_ptr<Implementation> implementation_;
};

} // namespace chimaera
