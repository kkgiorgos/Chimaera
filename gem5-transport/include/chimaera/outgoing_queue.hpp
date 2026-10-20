#pragma once

#include <chimaera/controller.hpp>
#include <algorithm>
#include <stdexcept>
#include <string>
#include <utility>
#include <span>

namespace chimaera::control {
inline constexpr std::size_t max_messages = 1024;
inline constexpr std::size_t max_bytes = 32 * 1024 * 1024;
// Keys are local queue metadata and never enter the wire protocol.
class OutgoingQueue {
public:
    struct Entry { Message data; std::string key; };
private:
    std::vector<Entry> entries_;
    std::size_t bytes_{};
    std::size_t message_limit_, byte_limit_;
public:
    explicit OutgoingQueue(std::size_t message_limit = max_messages,
                           std::size_t byte_limit = max_bytes)
        : message_limit_(message_limit), byte_limit_(byte_limit) {
        if (!message_limit || !byte_limit)
            throw std::invalid_argument("queue limits must be positive");
    }
    std::size_t size() const { return entries_.size(); }
    const Entry* front() const { return entries_.empty() ? nullptr : &entries_.front(); }
    std::span<const Entry> entries() const { return entries_; }
    void submit(Message data, std::string_view key = {}) {
        std::string owned_key(key);
        auto count = bytes_;
        auto size = entries_.size();
        if (!key.empty()) {
            for (const auto& entry : entries_) {
                if (entry.key == key) {
                    count -= entry.data.size();
                    --size;
                }
            }
        }
        if (size >= message_limit_ || data.size() > byte_limit_ - count)
            throw std::runtime_error("controller queue limit exceeded");
        // Allocate before changing the queue, including when replacement occurs.
        if (size + 1 > entries_.capacity())
            entries_.reserve(std::min(message_limit_,
                std::max(std::size_t{16}, entries_.capacity() * 2)));
        if (!key.empty())
            std::erase_if(entries_, [&](const Entry& entry) { return entry.key == key; });
        bytes_ = count + data.size();
        entries_.push_back({std::move(data), std::move(owned_key)});
    }
    std::optional<Entry> take() {
        if (entries_.empty()) return std::nullopt;
        auto entry = std::move(entries_.front());
        entries_.erase(entries_.begin());
        bytes_ -= entry.data.size();
        return entry;
    }
    std::vector<Message> drain() {
        std::vector<Message> batch;
        batch.reserve(entries_.size());
        for (auto& entry : entries_) batch.push_back(std::move(entry.data));
        entries_.clear();
        bytes_ = 0;
        return batch;
    }
};
} // namespace chimaera::control
