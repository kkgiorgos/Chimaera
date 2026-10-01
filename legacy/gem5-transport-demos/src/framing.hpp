#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace chimaera::detail {
inline constexpr std::uint64_t max_message_size = 64 * 1024 * 1024;
using Header = std::array<std::byte, 8>;

inline Header encode(std::uint64_t size) {
    Header header{};
    for (int i = 7; i >= 0; --i) {
        header[i] = static_cast<std::byte>(size & 0xff);
        size >>= 8;
    }
    return header;
}

inline std::uint64_t decode(const Header& header) {
    std::uint64_t size = 0;
    for (auto byte : header) size = (size << 8) | std::to_integer<unsigned>(byte);
    return size;
}
} // namespace chimaera::detail
