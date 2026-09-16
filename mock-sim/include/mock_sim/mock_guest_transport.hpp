#pragma once

#include <mock_sim/transport.hpp>
#include <memory>

namespace mock_sim {

namespace detail { class SocketTransport; }

class MockGuestTransport final : public Transport {
public:
    explicit MockGuestTransport(std::string endpoint);
    ~MockGuestTransport() override;

    MockGuestTransport(const MockGuestTransport&) = delete;
    MockGuestTransport& operator=(const MockGuestTransport&) = delete;

    [[nodiscard]] SendResult send(std::span<const std::byte> data) override;
    [[nodiscard]] ReceiveResult receive() override;

private:
    std::unique_ptr<detail::SocketTransport> implementation_;
};

} // namespace mock_sim
