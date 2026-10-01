#include <chimaera/queue_manager.hpp>

#include <algorithm>
#include <array>
#include <condition_variable>
#include <deque>
#include <limits>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <stdexcept>

namespace chimaera {
namespace {
constexpr std::array magic{std::byte{'C'}, std::byte{'Q'}, std::byte{'M'}, std::byte{1}};
constexpr std::size_t header_size = 12;
constexpr std::size_t record_size = 12;

struct Channel {
    explicit Channel(std::size_t capacity) : depth(capacity) {}
    const std::size_t depth;
    mutable std::mutex mutex;
    std::condition_variable changed;
    std::deque<QueueMessage> active;
    std::deque<QueueMessage> snapshot;
    std::size_t wire_bytes = 0;
    bool closed = false;
};

using Channels = std::map<ChannelId, std::unique_ptr<Channel>>;
Channels configure(std::span<const QueueChannel> config) {
    Channels channels;
    for (const auto& entry : config) {
        if (!entry.depth || channels.contains(entry.id))
            throw std::invalid_argument("channel depths must be positive and IDs unique");
        channels.emplace(entry.id, std::make_unique<Channel>(entry.depth));
    }
    return channels;
}

Channel& lookup(const Channels& channels, ChannelId id) {
    auto found = channels.find(id);
    if (found == channels.end()) throw std::out_of_range("unknown queue channel");
    return *found->second;
}

std::size_t checked_add(std::size_t a, std::size_t b) {
    if (b > std::numeric_limits<std::size_t>::max() - a)
        throw std::length_error("queue bundle is too large");
    return a + b;
}

void write_integer(QueueBundle& out, std::size_t& offset, std::uint64_t value, unsigned width) {
    for (unsigned i = width; i > 0; --i)
        out[offset++] = static_cast<std::byte>((value >> ((i - 1) * 8)) & 0xff);
}

std::uint64_t read_integer(std::span<const std::byte> in, std::size_t& offset, unsigned width) {
    if (in.size() - offset < width) throw std::invalid_argument("truncated queue bundle");
    std::uint64_t value = 0;
    for (unsigned i = 0; i < width; ++i)
        value = (value << 8) | std::to_integer<unsigned>(in[offset++]);
    return value;
}

std::optional<QueueMessage> pop(Channel& channel) {
    if (channel.active.empty()) return std::nullopt;
    QueueMessage result = std::move(channel.active.front());
    channel.active.pop_front();
    channel.changed.notify_all();
    return result;
}
} // namespace

struct QueueSerializer::Impl {
    explicit Impl(std::span<const QueueChannel> config) : channels(configure(config)) {}
    Channels channels;
    mutable std::shared_mutex gate;
    std::mutex serialization;
};

QueueSerializer::QueueSerializer(std::span<const QueueChannel> channels)
    : impl_(std::make_unique<Impl>(channels)) {}
QueueSerializer::~QueueSerializer() = default;

bool QueueSerializer::try_enqueue(ChannelId id, std::span<const std::byte> data) {
    auto& channel = lookup(impl_->channels, id);
    std::shared_lock gate(impl_->gate);
    std::lock_guard lock(channel.mutex);
    if (channel.active.size() == channel.depth) return false;
    const auto bytes = checked_add(channel.wire_bytes, checked_add(record_size, data.size()));
    channel.active.emplace_back(data.begin(), data.end());
    channel.wire_bytes = bytes;
    return true;
}

std::size_t QueueSerializer::size(ChannelId id) const {
    auto& channel = lookup(impl_->channels, id);
    std::shared_lock gate(impl_->gate);
    std::lock_guard lock(channel.mutex);
    return channel.active.size();
}

QueueBundle QueueSerializer::serialize() {
    std::lock_guard serialize_lock(impl_->serialization);
    QueueBundle bundle;
    std::size_t count = 0;
    {
        std::unique_lock gate(impl_->gate);
        std::size_t bytes = header_size;
        for (const auto& [id, channel] : impl_->channels) {
            bytes = checked_add(bytes, channel->wire_bytes);
            count = checked_add(count, channel->active.size());
        }
        // Allocate before changing any queue, giving a strong exception guarantee.
        bundle.resize(bytes);
        for (auto& [id, channel] : impl_->channels) {
            channel->active.swap(channel->snapshot);
            channel->wire_bytes = 0;
        }
    }
    std::copy(magic.begin(), magic.end(), bundle.begin());
    std::size_t offset = magic.size();
    write_integer(bundle, offset, count, 8);
    for (auto& [id, channel] : impl_->channels) {
        for (const auto& message : channel->snapshot) {
            write_integer(bundle, offset, id, 4);
            write_integer(bundle, offset, message.size(), 8);
            std::copy(message.begin(), message.end(), bundle.begin() + offset);
            offset += message.size();
        }
        channel->snapshot.clear();
    }
    return bundle;
}

struct QueueDeserializer::Impl {
    explicit Impl(std::span<const QueueChannel> config) : channels(configure(config)) {}
    Channels channels;
    std::mutex deserialization;
    std::mutex lifecycle;
    bool closed = false;
};

QueueDeserializer::QueueDeserializer(std::span<const QueueChannel> channels)
    : impl_(std::make_unique<Impl>(channels)) {}
QueueDeserializer::~QueueDeserializer() = default;

bool QueueDeserializer::deserialize(std::span<const std::byte> bundle) {
    std::lock_guard deserialize_lock(impl_->deserialization);
    if (bundle.size() < header_size || !std::equal(magic.begin(), magic.end(), bundle.begin()))
        throw std::invalid_argument("invalid queue bundle magic or version");
    std::size_t offset = magic.size();
    const auto count = read_integer(bundle, offset, 8);
    if (count > (bundle.size() - header_size) / record_size)
        throw std::invalid_argument("invalid queue bundle record count");
    // First pass requires no allocations, and rejects invalid input atomically.
    for (std::uint64_t i = 0; i < count; ++i) {
        auto id = static_cast<ChannelId>(read_integer(bundle, offset, 4));
        const auto length = read_integer(bundle, offset, 8);
        if (!impl_->channels.contains(id)) throw std::invalid_argument("unknown bundle channel");
        if (length > bundle.size() - offset) throw std::invalid_argument("truncated queue payload");
        offset += static_cast<std::size_t>(length);
    }
    if (offset != bundle.size()) throw std::invalid_argument("trailing queue bundle bytes");
    {
        std::lock_guard lock(impl_->lifecycle);
        if (impl_->closed) return false;
    }
    offset = header_size;
    for (std::uint64_t i = 0; i < count; ++i) {
        auto& channel = lookup(impl_->channels, static_cast<ChannelId>(read_integer(bundle, offset, 4)));
        const auto length = static_cast<std::size_t>(read_integer(bundle, offset, 8));
        std::unique_lock lock(channel.mutex);
        channel.changed.wait(lock, [&] { return channel.closed || channel.active.size() < channel.depth; });
        if (channel.closed) return false;
        channel.active.emplace_back(bundle.begin() + offset, bundle.begin() + offset + length);
        offset += length;
        lock.unlock();
        channel.changed.notify_all();
    }
    return true;
}

std::optional<QueueMessage> QueueDeserializer::try_dequeue(ChannelId id) {
    auto& channel = lookup(impl_->channels, id);
    std::lock_guard lock(channel.mutex);
    return pop(channel);
}

std::optional<QueueMessage> QueueDeserializer::wait_dequeue(ChannelId id) {
    auto& channel = lookup(impl_->channels, id);
    std::unique_lock lock(channel.mutex);
    channel.changed.wait(lock, [&] { return channel.closed || !channel.active.empty(); });
    return pop(channel);
}

std::size_t QueueDeserializer::size(ChannelId id) const {
    auto& channel = lookup(impl_->channels, id);
    std::lock_guard lock(channel.mutex);
    return channel.active.size();
}

void QueueDeserializer::close() {
    std::lock_guard lifecycle(impl_->lifecycle);
    impl_->closed = true;
    for (auto& [id, channel] : impl_->channels) {
        {
            std::lock_guard lock(channel->mutex);
            channel->closed = true;
        }
        channel->changed.notify_all();
    }
}
} // namespace chimaera
