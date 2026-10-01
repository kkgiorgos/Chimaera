#pragma once

#include <cstddef>
#include <vector>

namespace example {

// Exercise empty messages, embedded NULs, all byte values, and a larger payload.
inline std::vector<std::vector<std::byte>> messages() {
    std::vector<std::vector<std::byte>> result{
        {}, {std::byte{0x48}, std::byte{0x00}, std::byte{0x69}, std::byte{0xff}}};
    auto& large = result.emplace_back(64 * 1024);
    for (std::size_t i = 0; i < large.size(); ++i) {
        large[i] = static_cast<std::byte>(i % 256);
    }
    return result;
}

} // namespace example
