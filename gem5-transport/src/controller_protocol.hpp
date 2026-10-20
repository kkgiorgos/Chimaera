#pragma once

#include <chimaera/controller.hpp>
#include <chimaera/outgoing_queue.hpp>
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <utility>

namespace chimaera::control {
using Batch = std::vector<Message>;
// Version identification belongs to startup, not every message batch.
inline constexpr std::uint64_t startup_magic = 0x4348494d43545233; // CHIMCTR3
inline constexpr auto bootstrap_delay = std::chrono::milliseconds(1);
inline bool valid(Duration interval, Duration poll) {
    return interval.count() > 0 && poll.count() > 0 && poll <= interval &&
           interval <= std::chrono::hours(1) &&
           1 + (interval.count() - 1) / poll.count() <= 100000;
}
inline std::size_t bytes(const Batch& batch) {
    std::size_t count = 0;
    for (const auto& message : batch) {
        if (message.size() > max_bytes - count)
            throw std::runtime_error("controller queue exceeds 32 MiB");
        count += message.size();
    }
    if (batch.size() > max_messages)
        throw std::runtime_error("controller queue exceeds 1024 messages");
    return count;
}
// Received payloads keep their buffers. A cursor avoids moving the remaining
// message descriptors on each take; consumed slots are reclaimed when appending.
class ReceivedQueue {
    Batch messages_;
    std::size_t next_{};
    std::size_t bytes_{};
public:
    void append(Batch&& source) {
        const auto count = messages_.size() - next_;
        const auto added_bytes = bytes(source);
        if (count + source.size() > max_messages || added_bytes > max_bytes - bytes_)
            throw std::runtime_error("controller queue limit exceeded");
        if (source.empty()) return;
        const auto needed = count + source.size();
        // Allocate before compacting to preserve the queue on allocation failure.
        if (needed > messages_.capacity())
            messages_.reserve(std::max(needed, std::min(max_messages,
                std::max(std::size_t{16}, messages_.capacity() * 2))));
        if (next_) {
            std::move(messages_.begin() + next_, messages_.end(), messages_.begin());
            messages_.resize(count);
            next_ = 0;
        }
        for (auto& message : source) messages_.push_back(std::move(message));
        bytes_ += added_bytes;
        source.clear(); // Retain the caller's descriptor capacity for reuse.
    }
    std::optional<Message> take() {
        if (next_ == messages_.size()) return std::nullopt;
        bytes_ -= messages_[next_].size();
        auto message = std::move(messages_[next_++]);
        if (next_ == messages_.size()) {
            messages_.clear();
            next_ = 0;
        }
        return message;
    }
    Batch drain() {
        messages_.erase(messages_.begin(), messages_.begin() + next_);
        next_ = 0;
        bytes_ = 0;
        return std::exchange(messages_, Batch{});
    }
};
inline void put(Message& data, std::uint64_t value) {
    for (int shift = 56; shift >= 0; shift -= 8)
        data.push_back(static_cast<std::byte>((value >> shift) & 255));
}
inline std::uint64_t get(std::span<const std::byte>& data) {
    if (data.size() < 8) throw std::runtime_error("truncated controller packet");
    std::uint64_t value = 0;
    for (auto byte : data.first(8)) value = (value << 8) | std::to_integer<unsigned>(byte);
    data = data.subspan(8);
    return value;
}
inline Message startup_request() {
    Message data;
    put(data, startup_magic);
    return data;
}
// Zero means not ready. A positive duration completes startup and stays fixed.
inline Message encode_startup(Duration poll) {
    auto data = startup_request();
    put(data, poll.count());
    return data;
}
inline Duration decode_startup(std::span<const std::byte> data) {
    if (get(data) != startup_magic) throw std::runtime_error("controller protocol mismatch");
    const auto poll = get(data);
    if (!data.empty() || poll > static_cast<std::uint64_t>(Duration(std::chrono::hours(1)).count()))
        throw std::runtime_error("invalid controller startup reply");
    return Duration(poll);
}
inline Message encode_manifest(const Batch& batch) {
    bytes(batch);
    Message data;
    data.reserve(8 + batch.size() * 8);
    put(data, batch.size());
    for (const auto& message : batch) {
        put(data, message.size());
    }
    return data;
}
inline std::vector<std::size_t> decode_manifest(std::span<const std::byte> data) {
    const auto count = get(data);
    if (count > max_messages || data.size() != count * 8)
        throw std::runtime_error("invalid controller manifest");
    std::vector<std::size_t> sizes;
    sizes.reserve(count);
    std::size_t payload_bytes = 0;
    for (std::uint64_t i = 0; i < count; ++i) {
        const auto size = get(data);
        if (size > max_bytes - payload_bytes)
            throw std::runtime_error("controller batch exceeds 32 MiB");
        payload_bytes += size;
        sizes.push_back(size);
    }
    return sizes;
}
inline void send(Transport& transport, std::span<const std::byte> data) {
    auto result = transport.send(data);
    if (!result.ok()) throw std::runtime_error(result.message);
}
inline Message receive(Transport& transport) {
    auto result = transport.receive();
    if (!result.ok()) throw std::runtime_error(result.message);
    return std::move(result.data);
}

// A small manifest is followed by independently owned payload frames. Never
// concatenate image data into a batch and copy it back out at the destination.
// Startup v3 rejects peers using the previous contiguous-batch protocol.
inline void send_batch(Transport& transport, const Batch& batch) {
    const auto manifest = encode_manifest(batch); // Validate before sending any bytes.
    send(transport, manifest);
    for (const auto& message : batch) send(transport, message);
}

inline Batch receive_batch(Transport& transport) {
    const auto sizes = decode_manifest(receive(transport));
    Batch batch;
    batch.reserve(sizes.size());
    for (const auto size : sizes) {
        auto message = receive(transport);
        if (message.size() != size)
            throw std::runtime_error("controller payload does not match manifest");
        batch.push_back(std::move(message));
    }
    return batch; // No partial batch becomes visible on a transport failure.
}
} // namespace chimaera::control
