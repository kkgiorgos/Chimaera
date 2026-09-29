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
    if (ops_ == GuestM5Ops::address && !m5_mem)
        return {TransportError::invalid_argument, "libm5 address memory is not mapped"};
    const auto send_op = ops_ == GuestM5Ops::instruction ? m5_chimaera_send : m5_chimaera_send_addr;
    auto header = detail::encode(data.size());
    if (send_op(header.data(), header.size()) != header.size() ||
        (!data.empty() && send_op(
            const_cast<std::byte*>(data.data()), data.size()) != data.size())) {
        failure_ = {TransportError::io_error, "incomplete gem5 send"};
    }
    return failure_;
}

ReceiveResult GuestTransport::receive() {
    if (!failure_.ok()) return {{}, failure_.error, failure_.message};
    if (ops_ == GuestM5Ops::address && !m5_mem)
        return {{}, TransportError::invalid_argument, "libm5 address memory is not mapped"};
    const auto recv_op = ops_ == GuestM5Ops::instruction ? m5_chimaera_recv : m5_chimaera_recv_addr;
    detail::Header header{};
    if (recv_op(header.data(), header.size()) != header.size()) {
        failure_ = {TransportError::io_error, "incomplete gem5 receive header"};
    } else {
        const auto size = detail::decode(header);
        if (size > detail::max_message_size) {
            failure_ = {TransportError::invalid_argument, "peer message exceeds 64 MiB limit"};
        } else {
            try {
                std::vector<std::byte> data(static_cast<std::size_t>(size));
                if (data.empty() || recv_op(data.data(), size) == size)
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
