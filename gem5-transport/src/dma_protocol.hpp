#pragma once

#include <chimaera/outgoing_queue.hpp>
#include <cstring>
#include <cstdint>
#include <span>

namespace chimaera::dma {
inline constexpr char startup[] = "CHDMA002";
inline constexpr std::size_t header_bytes = 16;
inline constexpr std::size_t small_bytes = 4096;
inline constexpr std::size_t batch_bytes = 65536;

inline void put32(std::byte* output, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) output[i] = static_cast<std::byte>(value >> (24 - 8 * i));
}
inline std::uint32_t get32(const std::byte* input) {
    std::uint32_t result = 0;
    for (unsigned i = 0; i < 4; ++i) result = result << 8 | std::to_integer<unsigned>(input[i]);
    return result;
}
struct Layout {
    std::size_t count{}, payload{}, table{};
    std::size_t size() const { return header_bytes + table + payload; }
};
// Batch only consecutive small packets. Large images stay separate, and FIFO
// ordering across routes is unchanged. Each batch is bounded independently.
inline Layout layout(std::span<const control::OutgoingQueue::Entry> entries) {
    if (entries.empty()) return {};
    Layout result{1, entries.front().data.size(), 0};
    if (result.payload > small_bytes) return result;
    for (std::size_t i = 1; i < entries.size() && i < 64; ++i) {
        const auto size = entries[i].data.size();
        if (size > small_bytes || header_bytes + (i + 1) * 4 + result.payload + size > batch_bytes) break;
        ++result.count;
        result.payload += size;
    }
    result.table = result.count > 1 ? result.count * 4 : 0;
    return result;
}
inline void header(std::byte* output, const Layout& value) {
    std::memcpy(output, "CDM2", 4);
    put32(output + 4, value.count);
    put32(output + 8, value.payload);
    put32(output + 12, 0);
}
inline void encode(std::byte* output, const Layout& value,
                   std::span<const control::OutgoingQueue::Entry> entries) {
    header(output, value);
    auto offset = header_bytes + value.table;
    for (std::size_t i = 0; i < value.count; ++i) {
        const auto& bytes = entries[i].data;
        if (value.table) put32(output + header_bytes + i * 4, bytes.size());
        if (bytes.size()) std::memcpy(output + offset, bytes.data(), bytes.size());
        offset += bytes.size();
    }
}
inline std::vector<Message> decode(Message packet) {
    if (packet.size() < header_bytes || std::memcmp(packet.data(), "CDM2", 4) ||
        get32(packet.data() + 12) != 0)
        throw std::runtime_error("invalid DMA packet/version");
    const auto count = get32(packet.data() + 4);
    const auto payload = get32(packet.data() + 8);
    if (!count || count > 64) throw std::runtime_error("invalid DMA packet count");
    const std::size_t table = count > 1 ? count * 4 : 0;
    if (table && packet.size() > batch_bytes) throw std::runtime_error("DMA control batch exceeds limit");
    if (header_bytes + table > packet.size() || packet.size() - header_bytes - table != payload)
        throw std::runtime_error("truncated DMA packet");
    std::vector<std::size_t> sizes;
    sizes.reserve(count);
    std::size_t remaining = payload;
    for (unsigned i = 0; i < count; ++i) {
        const auto length = table ? get32(packet.data() + header_bytes + i * 4) : payload;
        if (length > remaining || (table && length > small_bytes))
            throw std::runtime_error("invalid DMA batch record length");
        remaining -= length;
        sizes.push_back(length);
    }
    if (remaining) throw std::runtime_error("unclaimed DMA packet payload");
    // Validate the whole packet before returning any slice to an application.
    std::vector<Message> result;
    result.reserve(count);
    auto offset = header_bytes + table;
    for (const auto size : sizes) {
        result.push_back(packet.slice(offset, size));
        offset += size;
    }
    return result;
}
} // namespace chimaera::dma
