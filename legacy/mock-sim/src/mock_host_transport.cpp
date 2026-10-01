#include <mock_sim/mock_host_transport.hpp>
#include "socket_transport.hpp"
#include <utility>

namespace mock_sim {

MockHostTransport::MockHostTransport(std::string endpoint)
    : implementation_(std::make_unique<detail::SocketTransport>(std::move(endpoint), true)) {}

MockHostTransport::~MockHostTransport() = default;

SendResult MockHostTransport::send(std::span<const std::byte> data) {
    return implementation_->send(data);
}

ReceiveResult MockHostTransport::receive() {
    return implementation_->receive();
}

int MockHostTransport::peer_process_id() const {
    return implementation_->peer_process_id();
}

} // namespace mock_sim
