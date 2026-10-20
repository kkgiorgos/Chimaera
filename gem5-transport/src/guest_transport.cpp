#include <chimaera/guest_transport.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include "framing.hpp"

#include <new>
#include <cstdint>
#include <utility>
#include <unistd.h>

namespace chimaera {
namespace {
void prefault_receive_buffer(std::span<std::byte> data) {
    if (data.empty()) return;
    static const auto page_size = [] {
        const auto value = ::sysconf(_SC_PAGESIZE);
        if (value <= 0) throw std::runtime_error("cannot determine guest page size");
        return static_cast<std::size_t>(value);
    }();
    // m5 functional writes translate guest page tables without running Linux's
    // page-fault handler. An untouched malloc/mmap allocation may be unmapped or
    // still backed by the shared zero page. Write once per page to establish
    // private writable mappings before m5 fills the payload. Touch actual page
    // boundaries so an unaligned buffer's final partial page is included.
    auto* bytes = reinterpret_cast<volatile std::byte*>(data.data());
    bytes[0] = std::byte{};
    const auto first_boundary = page_size - reinterpret_cast<std::uintptr_t>(data.data()) % page_size;
    for (std::size_t offset = first_boundary; offset < data.size(); offset += page_size)
        bytes[offset] = std::byte{};
}
} // namespace

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
                auto data = Message::for_overwrite(static_cast<std::size_t>(size));
                prefault_receive_buffer(data);
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
