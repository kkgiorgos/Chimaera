#pragma once

#include <algorithm>
#include <cstddef>
#include <initializer_list>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <utility>
#include <vector>

namespace chimaera {

// An owning contiguous byte buffer with value semantics. Ordinary construction
// and resize zero-initialize new bytes, as std::vector did. Only receive buffers
// use for_overwrite: every byte must be filled before the message is exposed.
// This avoids simulating a full image-sized memset immediately before m5/DDS
// overwrites those same bytes. Moving a message always transfers its allocation.
class Message {
    std::unique_ptr<std::byte[]> data_;
    std::size_t size_{};
    std::size_t capacity_{};

public:
    using value_type = std::byte;
    using iterator = std::byte*;
    using const_iterator = const std::byte*;

    Message() = default;
    explicit Message(std::size_t size, std::byte value = {}) : Message(for_overwrite(size)) {
        if (size) std::fill_n(data(), size, value);
    }
    template<std::forward_iterator I>
    Message(I first, I last) : Message(for_overwrite(first == last ? 0 : std::distance(first, last))) {
        if (!empty()) std::copy(first, last, begin());
    }
    Message(std::initializer_list<std::byte> values) : Message(values.begin(), values.end()) {}
    Message(const std::vector<std::byte>& values) : Message(values.begin(), values.end()) {}
    Message(const Message& other) : Message(other.begin(), other.end()) {}
    Message(Message&& other) noexcept
        : data_(std::move(other.data_)), size_(std::exchange(other.size_, 0)),
          capacity_(std::exchange(other.capacity_, 0)) {}
    Message& operator=(Message other) noexcept { swap(other); return *this; }

    static Message for_overwrite(std::size_t size) {
        Message message;
        if (size) message.data_ = std::make_unique_for_overwrite<std::byte[]>(size);
        message.size_ = message.capacity_ = size;
        return message;
    }
    void swap(Message& other) noexcept {
        data_.swap(other.data_);
        std::swap(size_, other.size_);
        std::swap(capacity_, other.capacity_);
    }
    std::byte* data() noexcept { return data_.get(); }
    const std::byte* data() const noexcept { return data_.get(); }
    std::size_t size() const noexcept { return size_; }
    std::size_t capacity() const noexcept { return capacity_; }
    bool empty() const noexcept { return size_ == 0; }
    iterator begin() noexcept { return data(); }
    const_iterator begin() const noexcept { return data(); }
    iterator end() noexcept { return size_ ? data() + size_ : data(); }
    const_iterator end() const noexcept { return size_ ? data() + size_ : data(); }
    std::byte& operator[](std::size_t index) { return data_[index]; }
    const std::byte& operator[](std::size_t index) const { return data_[index]; }
    std::byte& front() { return data_[0]; }
    const std::byte& front() const { return data_[0]; }
    std::byte& back() { return data_[size_ - 1]; }
    const std::byte& back() const { return data_[size_ - 1]; }
    void clear() noexcept { size_ = 0; }
    void reserve(std::size_t capacity) {
        if (capacity <= capacity_) return;
        auto replacement = std::make_unique_for_overwrite<std::byte[]>(capacity);
        if (!empty()) std::copy(begin(), end(), replacement.get());
        data_ = std::move(replacement);
        capacity_ = capacity;
    }
    void resize(std::size_t size, std::byte value = {}) {
        reserve(size);
        if (size > size_) std::fill(data() + size_, data() + size, value);
        size_ = size;
    }
    void push_back(std::byte value) {
        if (size_ == static_cast<std::size_t>(-1)) throw std::length_error("message too large");
        if (size_ == capacity_) reserve(capacity_ == 0 ? 16 :
            capacity_ <= static_cast<std::size_t>(-1) / 2 ? capacity_ * 2 : size_ + 1);
        data_[size_++] = value;
    }
    void pop_back() { --size_; }
    template<std::forward_iterator I>
    iterator insert(const_iterator position, I first, I last) {
        const auto offset = size_ ? static_cast<std::size_t>(position - data()) : 0;
        const auto count = first == last ? 0 : static_cast<std::size_t>(std::distance(first, last));
        if (!count) return size_ ? data() + offset : data();
        if (count > static_cast<std::size_t>(-1) - size_) throw std::length_error("message too large");
        reserve(size_ + count);
        auto target = data() + offset;
        std::move_backward(target, data() + size_, data() + size_ + count);
        std::copy(first, last, target);
        size_ += count;
        return target;
    }
    friend bool operator==(const Message& left, const Message& right) {
        return left.size() == right.size() && (left.empty() ||
            std::equal(left.begin(), left.end(), right.begin()));
    }
};
} // namespace chimaera
