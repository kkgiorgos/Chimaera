#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <vector>

namespace chimaera {

using ChannelId = std::uint32_t;
using QueueMessage = std::vector<std::byte>;
using QueueBundle = std::vector<std::byte>;

struct QueueChannel {
    ChannelId id;
    std::size_t depth; // Maximum number of messages, including empty messages.
};

// Configuration is immutable. Unknown IDs throw std::out_of_range; duplicate
// IDs and zero depths throw std::invalid_argument. Objects are not movable.
class QueueSerializer {
public:
    explicit QueueSerializer(std::span<const QueueChannel> channels);
    ~QueueSerializer();
    QueueSerializer(const QueueSerializer&) = delete;
    QueueSerializer& operator=(const QueueSerializer&) = delete;

    // Copies data on success. Returns false if this channel's active queue is full.
    [[nodiscard]] bool try_enqueue(ChannelId channel, std::span<const std::byte> data);
    [[nodiscard]] std::size_t size(ChannelId channel) const;

    // Atomically detaches all active queues, then encodes them without blocking
    // producers. Concurrent serialize calls are serialized. Allocation failure
    // before detachment leaves all queues unchanged. Empty snapshots are valid.
    [[nodiscard]] QueueBundle serialize();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

class QueueDeserializer {
public:
    explicit QueueDeserializer(std::span<const QueueChannel> channels);
    ~QueueDeserializer();
    QueueDeserializer(const QueueDeserializer&) = delete;
    QueueDeserializer& operator=(const QueueDeserializer&) = delete;

    // Validates the entire bundle before publishing messages. Throws
    // std::invalid_argument on malformed bundles or unknown channel IDs.
    // Publishes in wire order, waiting for room in full queues. Concurrent calls
    // are serialized. Input must remain valid and unchanged until this returns.
    // Returns false on close; a prefix may already have been published.
    [[nodiscard]] bool deserialize(std::span<const std::byte> bundle);
    [[nodiscard]] std::optional<QueueMessage> try_dequeue(ChannelId channel);
    // Waits for data or close. Queued data remains drainable after close.
    [[nodiscard]] std::optional<QueueMessage> wait_dequeue(ChannelId channel);
    [[nodiscard]] std::size_t size(ChannelId channel) const;

    // Wakes blocked producers and consumers. Idempotent; cannot be reopened.
    // Call close and join all users before destroying this object.
    void close();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace chimaera
