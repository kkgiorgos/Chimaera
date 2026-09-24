#pragma once

#include <mock_sim/transport.hpp>
#include <memory>

namespace mock_sim {

namespace detail { class SocketTransport; }

class MockHostTransport final : public Transport {
public:
    // Both processes use the same Unix-domain socket path.
    explicit MockHostTransport(std::string endpoint);
    ~MockHostTransport() override;

    MockHostTransport(const MockHostTransport&) = delete;
    MockHostTransport& operator=(const MockHostTransport&) = delete;

    [[nodiscard]] SendResult send(std::span<const std::byte> data) override;
    [[nodiscard]] ReceiveResult receive() override;

    // Linux peer credentials, for the mock controller's process-level run gate.
    // Returns -1 until connected or if credentials cannot be read.
    [[nodiscard]] int peer_process_id() const;

private:
    std::unique_ptr<detail::SocketTransport> implementation_;
};

} // namespace mock_sim
