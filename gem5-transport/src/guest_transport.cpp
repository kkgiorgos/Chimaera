#include <chimaera/guest_transport.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include "framing.hpp"

#include <new>
#include <utility>

namespace chimaera {

SendResult GuestTransport::send(std::span<const std::byte> data) {
    if (!failure_.ok()) return failure_;
    if (data.size() > detail::max_message_size)
        return {TransportError::invalid_argument, "message exceeds 64 MiB limit"};
    if (!m5_mem)
        return {TransportError::invalid_argument, "libm5 address memory is not mapped"};
    auto header = detail::encode(data.size());
    if (m5_chimaera_send_addr(header.data(), header.size()) != header.size() ||
        (!data.empty() && m5_chimaera_send_addr(
            const_cast<std::byte*>(data.data()), data.size()) != data.size())) {
        failure_ = {TransportError::io_error, "incomplete gem5 send"};
    }
    return failure_;
}

ReceiveResult GuestTransport::receive() {
    if (!failure_.ok()) return {{}, failure_.error, failure_.message};
    if (!m5_mem)
        return {{}, TransportError::invalid_argument, "libm5 address memory is not mapped"};
    detail::Header header{};
    if (m5_chimaera_recv_addr(header.data(), header.size()) != header.size()) {
        failure_ = {TransportError::io_error, "incomplete gem5 receive header"};
    } else {
        const auto size = detail::decode(header);
        if (size > detail::max_message_size) {
            failure_ = {TransportError::invalid_argument, "peer message exceeds 64 MiB limit"};
        } else {
            try {
                std::vector<std::byte> data(static_cast<std::size_t>(size));
                if (data.empty() || m5_chimaera_recv_addr(data.data(), size) == size)
                    return {std::move(data), TransportError::none, {}};
                failure_ = {TransportError::io_error, "incomplete gem5 receive payload"};
            } catch (const std::bad_alloc&) {
                failure_ = {TransportError::io_error, "cannot allocate receive buffer"};
            }
        }
    }
    return {{}, failure_.error, failure_.message};
}
} // namespace chimaera
