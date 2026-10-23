/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_MAILBOX_HH
#define GEM5_CHIMAERA_MAILBOX_HH

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <new>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

#include "gem5/chimaera_dma.h"

namespace chimaera_mailbox {
// One producer and one consumer. Simulation boundary fences determine virtual
// admission time; these atomics protect publication of complete host buffers.
class Mailbox {
    struct alignas(64) Header {
        std::uint64_t magic{0x4348444d41424f58ULL};
        std::uint32_t version{1}, slots{2}, capacity{CHDMA_MAX_BYTES}, reserved{};
        alignas(64) std::atomic<std::uint64_t> written{0};
        alignas(64) std::atomic<std::uint64_t> consumed{0};
    };
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
                  "mailboxes require lock-free shared 64-bit atomics");
    std::string path_;
    int fd_{-1};
    void* memory_{MAP_FAILED};
    std::size_t mapping_size_{};
    bool owner_{};
    struct stat identity_{};
    Header* header_{};

    static std::size_t stride(std::size_t bytes) { return (bytes + 64 + 63) & ~std::size_t{63}; }
    std::uint8_t* slot(std::uint64_t index) const {
        return static_cast<std::uint8_t*>(memory_) + 4096 +
            index % header_->slots * stride(header_->capacity);
    }
    void cleanup() noexcept {
        if (memory_ != MAP_FAILED) ::munmap(memory_, mapping_size_);
        if (fd_ >= 0) ::close(fd_);
        struct stat current{};
        if (owner_ && ::lstat(path_.c_str(), &current) == 0 &&
            current.st_dev == identity_.st_dev && current.st_ino == identity_.st_ino)
            ::unlink(path_.c_str());
    }
public:
    struct View { const std::uint8_t* data; std::size_t size; };
    explicit Mailbox(std::string path, bool create = false,
                     std::uint32_t slots = 2, std::uint32_t capacity = CHDMA_MAX_BYTES)
        : path_(std::move(path)) {
        try {
            if (!slots || slots > 64 || !capacity || capacity > CHDMA_MAX_BYTES)
                throw std::runtime_error("invalid mailbox geometry");
            fd_ = ::open(path_.c_str(), O_RDWR | O_CLOEXEC | (create ? O_CREAT | O_EXCL : 0), 0600);
            if (fd_ < 0) throw std::runtime_error("open mailbox " + path_ + ": " + std::strerror(errno));
            if (::fstat(fd_, &identity_) < 0) throw std::runtime_error("stat mailbox failed");
            owner_ = create;
            mapping_size_ = 4096 + slots * stride(capacity);
            if (create && ::ftruncate(fd_, mapping_size_) < 0)
                throw std::runtime_error("size mailbox failed");
            if (!create && static_cast<std::uint64_t>(identity_.st_size) != mapping_size_)
                throw std::runtime_error("mailbox size mismatch");
            memory_ = ::mmap(nullptr, mapping_size_, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
            if (memory_ == MAP_FAILED) throw std::runtime_error("map mailbox failed");
            header_ = static_cast<Header*>(memory_);
            if (create) {
                new (header_) Header;
                header_->slots = slots;
                header_->capacity = capacity;
            } else if (header_->magic != 0x4348444d41424f58ULL || header_->version != 1 ||
                       header_->slots != slots || header_->capacity != capacity) {
                throw std::runtime_error("mailbox ABI mismatch");
            }
        } catch (...) { cleanup(); throw; }
    }
    ~Mailbox() { cleanup(); }
    Mailbox(const Mailbox&) = delete;
    Mailbox& operator=(const Mailbox&) = delete;
    std::uint8_t* reserve(std::size_t size) {
        if (size > header_->capacity) throw std::runtime_error("mailbox payload too large");
        const auto written = header_->written.load(std::memory_order_relaxed);
        const auto consumed = header_->consumed.load(std::memory_order_acquire);
        if (written - consumed > header_->slots) throw std::runtime_error("corrupt mailbox counters");
        return written - consumed == header_->slots ? nullptr : slot(written) + 64;
    }
    void commit(std::size_t size) {
        if (!reserve(size)) throw std::runtime_error("mailbox full");
        const auto written = header_->written.load(std::memory_order_relaxed);
        *reinterpret_cast<std::uint32_t*>(slot(written)) = size;
        header_->written.store(written + 1, std::memory_order_release);
    }
    bool write(const void* data, std::size_t size) {
        auto* destination = reserve(size);
        if (!destination) return false;
        if (size) std::memcpy(destination, data, size);
        commit(size);
        return true;
    }
    bool peek(View& view) const {
        const auto consumed = header_->consumed.load(std::memory_order_relaxed);
        const auto written = header_->written.load(std::memory_order_acquire);
        if (written - consumed > header_->slots) throw std::runtime_error("corrupt mailbox counters");
        if (written == consumed) return false;
        const auto size = *reinterpret_cast<const std::uint32_t*>(slot(consumed));
        if (size > header_->capacity) throw std::runtime_error("corrupt mailbox payload");
        view = {slot(consumed) + 64, size};
        return true;
    }
    void pop() {
        View view{};
        if (!peek(view)) throw std::runtime_error("mailbox empty");
        const auto consumed = header_->consumed.load(std::memory_order_relaxed);
        header_->consumed.store(consumed + 1, std::memory_order_release);
    }
    // Checkpoint caller must pause both producer and consumer. This does not
    // consume credits or retain pointers into the external mapped ring.
    std::vector<std::vector<std::uint8_t>> snapshot() const {
        const auto consumed = header_->consumed.load(std::memory_order_acquire);
        const auto written = header_->written.load(std::memory_order_acquire);
        if (written - consumed > header_->slots) throw std::runtime_error("corrupt mailbox counters");
        std::vector<std::vector<std::uint8_t>> result;
        for (auto index = consumed; index != written; ++index) {
            const auto size = *reinterpret_cast<const std::uint32_t*>(slot(index));
            if (size > header_->capacity) throw std::runtime_error("corrupt mailbox payload");
            result.emplace_back(slot(index) + 64, slot(index) + 64 + size);
        }
        return result;
    }
};
} // namespace chimaera_mailbox
#endif
