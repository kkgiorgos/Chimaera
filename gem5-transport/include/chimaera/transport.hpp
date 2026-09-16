#pragma once

#include <cstddef>
#include <span>
#include <string>
#include <vector>

namespace chimaera {

enum class TransportError {
    none,
    not_implemented,
    invalid_argument,
    disconnected,
    io_error,
};

struct SendResult {
    TransportError error{TransportError::none};
    std::string message;

    [[nodiscard]] bool ok() const noexcept { return error == TransportError::none; }
};

struct ReceiveResult {
    std::vector<std::byte> data;
    TransportError error{TransportError::none};
    std::string message;

    [[nodiscard]] bool ok() const noexcept { return error == TransportError::none; }
};

// Sequential, synchronous message transport. Callers must serialize operations;
// implementations need not support concurrent calls or multiple in-flight sends.
class Transport {
public:
    virtual ~Transport() = default;

    // Success means the complete message was sent (including an empty message).
    // The implementation must not retain data after this call returns.
    [[nodiscard]] virtual SendResult send(std::span<const std::byte> data) = 0;

    // Wait for one complete message or an error. The result owns its bytes.
    // On error, data is empty. Message boundaries must be preserved.
    [[nodiscard]] virtual ReceiveResult receive() = 0;
};

} // namespace chimaera
