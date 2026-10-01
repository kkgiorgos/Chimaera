#pragma once

#include <mock_sim/transport.hpp>

#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <new>
#include <thread>
#include <utility>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace mock_sim::detail {

// Shared private implementation; both public libraries remain independently linkable.
class SocketTransport {
public:
    SocketTransport(std::string endpoint, bool host)
        : endpoint_(std::move(endpoint)), host_(host) {}

    ~SocketTransport() {
        close_connection();
        if (listener_ >= 0) ::close(listener_);
        struct stat current {};
        if (owns_endpoint_ && ::lstat(endpoint_.c_str(), &current) == 0 &&
            current.st_dev == endpoint_stat_.st_dev && current.st_ino == endpoint_stat_.st_ino) {
            ::unlink(endpoint_.c_str());
        }
    }

    SocketTransport(const SocketTransport&) = delete;
    SocketTransport& operator=(const SocketTransport&) = delete;

    int peer_process_id() const {
        ucred credentials{};
        socklen_t size = sizeof(credentials);
        if (connection_ < 0 || ::getsockopt(connection_, SOL_SOCKET, SO_PEERCRED,
                                           &credentials, &size) < 0) return -1;
        return credentials.pid;
    }

    SendResult send(std::span<const std::byte> data) {
        if (data.size() > max_message_size) {
            return {TransportError::invalid_argument, "message exceeds 64 MiB limit"};
        }
        if (!connect_peer()) return failure_;
        std::array<std::byte, 8> header{};
        auto size = static_cast<std::uint64_t>(data.size());
        for (int i = 7; i >= 0; --i) {
            header[i] = static_cast<std::byte>(size & 0xff);
            size >>= 8;
        }
        if (!write_all(header) || !write_all(data)) return failure_;
        return {};
    }

    ReceiveResult receive() {
        if (!connect_peer()) return {{}, failure_.error, failure_.message};
        std::array<std::byte, 8> header{};
        if (!read_all(header)) return {{}, failure_.error, failure_.message};
        std::uint64_t size = 0;
        for (auto byte : header) size = (size << 8) | std::to_integer<unsigned>(byte);
        if (size > max_message_size) {
            fail(TransportError::invalid_argument, "peer message exceeds 64 MiB limit");
            return {{}, failure_.error, failure_.message};
        }
        std::vector<std::byte> data;
        try {
            data.resize(static_cast<std::size_t>(size));
        } catch (const std::bad_alloc&) {
            fail(TransportError::io_error, "cannot allocate receive buffer");
            return {{}, failure_.error, failure_.message};
        }
        if (!read_all(data)) return {{}, failure_.error, failure_.message};
        return {std::move(data), TransportError::none, {}};
    }

private:
    static constexpr std::uint64_t max_message_size = 64 * 1024 * 1024;
    std::string endpoint_;
    bool host_;
    int connection_{-1};
    int listener_{-1};
    bool owns_endpoint_{false};
    struct stat endpoint_stat_ {};
    SendResult failure_;

    void close_connection() {
        if (connection_ >= 0) ::close(connection_);
        connection_ = -1;
    }

    bool fail(TransportError error, std::string message) {
        failure_ = {error, std::move(message)};
        close_connection(); // A partial frame cannot be retried on this stream.
        return false;
    }

    bool system_failure(const char* operation) {
        const int code = errno;
        const auto error = code == EPIPE || code == ECONNRESET || code == ENOTCONN
            ? TransportError::disconnected : TransportError::io_error;
        return fail(error, std::string(operation) + ": " + std::strerror(code));
    }

    bool connect_peer() {
        if (!failure_.ok()) return false;
        if (connection_ >= 0) return true;
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        if (endpoint_.empty() || endpoint_.size() >= sizeof(address.sun_path) ||
            endpoint_.find('\0') != std::string::npos) {
            return fail(TransportError::invalid_argument, "invalid Unix socket path");
        }
        std::memcpy(address.sun_path, endpoint_.c_str(), endpoint_.size() + 1);
        if (host_) {
            listener_ = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
            if (listener_ < 0) return system_failure("socket");
            // Never unlink an existing endpoint: it may belong to a live host.
            if (::bind(listener_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0)
                return system_failure("bind");
            if (::lstat(endpoint_.c_str(), &endpoint_stat_) < 0) return system_failure("lstat");
            owns_endpoint_ = true;
            if (::listen(listener_, 1) < 0) return system_failure("listen");
            do {
                connection_ = ::accept4(listener_, nullptr, nullptr, SOCK_CLOEXEC);
            } while (connection_ < 0 && errno == EINTR);
            if (connection_ < 0) return system_failure("accept");
            ::close(listener_);
            listener_ = -1;
            return true;
        }
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (true) {
            connection_ = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
            if (connection_ < 0) return system_failure("socket");
            if (::connect(connection_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0)
                return true;
            const int code = errno;
            if (code != ENOENT && code != ECONNREFUSED && code != EINTR)
                return system_failure("connect");
            close_connection();
            if (std::chrono::steady_clock::now() >= deadline)
                return fail(TransportError::disconnected, "host unavailable after five seconds");
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }

    bool write_all(std::span<const std::byte> bytes) {
        while (!bytes.empty()) {
            const auto count = ::send(connection_, bytes.data(), bytes.size(), MSG_NOSIGNAL);
            if (count < 0) {
                if (errno == EINTR) continue;
                return system_failure("send");
            }
            if (count == 0) return fail(TransportError::disconnected, "peer disconnected during send");
            bytes = bytes.subspan(static_cast<std::size_t>(count));
        }
        return true;
    }

    bool read_all(std::span<std::byte> bytes) {
        while (!bytes.empty()) {
            const auto count = ::recv(connection_, bytes.data(), bytes.size(), 0);
            if (count < 0) {
                if (errno == EINTR) continue;
                return system_failure("receive");
            }
            if (count == 0) return fail(TransportError::disconnected, "peer disconnected during receive");
            bytes = bytes.subspan(static_cast<std::size_t>(count));
        }
        return true;
    }
};

} // namespace mock_sim::detail
