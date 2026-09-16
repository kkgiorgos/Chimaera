#include <mock_sim/mock_guest_transport.hpp>
#include "socket_transport.hpp"
#include <utility>

namespace mock_sim {

MockGuestTransport::MockGuestTransport(std::string endpoint)
    : implementation_(std::make_unique<detail::SocketTransport>(std::move(endpoint), false)) {}

MockGuestTransport::~MockGuestTransport() = default;

SendResult MockGuestTransport::send(std::span<const std::byte> data) {
    return implementation_->send(data);
}

ReceiveResult MockGuestTransport::receive() {
    return implementation_->receive();
}

} // namespace mock_sim
