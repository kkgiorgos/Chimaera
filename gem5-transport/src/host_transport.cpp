#include <chimaera/host_transport.hpp>
#include "framing.hpp"

#include <cerrno>
#include <cstring>
#include <new>
#include <utility>
#include <poll.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace chimaera {
namespace {
struct Listener {
    std::string path;
    int fd{-1};
    bool owned{false};
    struct stat identity {};
    explicit Listener(std::string name) : path(std::move(name)) {}
    ~Listener() {
        if (fd >= 0) ::close(fd);
        struct stat current {};
        if (owned && ::lstat(path.c_str(), &current) == 0 &&
            current.st_dev == identity.st_dev && current.st_ino == identity.st_ino)
            ::unlink(path.c_str());
    }
};
struct Connection {
    int fd;
    ~Connection() { if (fd >= 0) ::close(fd); }
};
} // namespace

class HostTransport::Implementation {
public:
    Implementation(std::string g2h, std::string h2g)
        : incoming_(std::move(g2h)), outgoing_(std::move(h2g)) {
        if (cancel_fd_.fd < 0) { system_failure("eventfd"); return; }
        if (open(incoming_)) open(outgoing_);
    }

    void cancel() noexcept {
        const std::uint64_t value = 1;
        while (::write(cancel_fd_.fd, &value, sizeof(value)) < 0 && errno == EINTR) {}
    }

    SendResult send(std::span<const std::byte> data) {
        if (!failure_.ok()) return failure_;
        if (data.size() > detail::max_message_size)
            return {TransportError::invalid_argument, "message exceeds 64 MiB limit"};
        const auto header = detail::encode(data.size());
        if (!send_packet(header)) return failure_;
        if (!data.empty()) send_packet(data);
        return failure_;
    }

    ReceiveResult receive() {
        if (!failure_.ok()) return {{}, failure_.error, failure_.message};
        detail::Header header{};
        if (receive_packet(header)) {
            const auto size = detail::decode(header);
            if (size > detail::max_message_size) {
                fail(TransportError::invalid_argument, "peer message exceeds 64 MiB limit");
            } else {
                try {
                    std::vector<std::byte> data(static_cast<std::size_t>(size));
                    if (data.empty() || receive_packet(data))
                        return {std::move(data), TransportError::none, {}};
                } catch (const std::bad_alloc&) {
                    fail(TransportError::io_error, "cannot allocate receive buffer");
                }
            }
        }
        return {{}, failure_.error, failure_.message};
    }

private:
    Listener incoming_;
    Listener outgoing_;
    SendResult failure_;
    Connection cancel_fd_{::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK)};

    bool ready(int fd, short events) {
        pollfd descriptors[]{{fd, events, 0}, {cancel_fd_.fd, POLLIN, 0}};
        int result;
        do { result = ::poll(descriptors, 2, -1); } while (result < 0 && errno == EINTR);
        if (result < 0) return system_failure("poll");
        if (descriptors[1].revents)
            return fail(TransportError::disconnected, "transport cancelled");
        return true; // EOF/socket errors are reported by the following I/O call.
    }

    bool fail(TransportError error, std::string message) {
        failure_ = {error, std::move(message)};
        return false;
    }
    bool system_failure(const char* operation) {
        const int code = errno;
        return fail(code == EPIPE || code == ECONNRESET || code == ENOTCONN
                        ? TransportError::disconnected : TransportError::io_error,
                    std::string(operation) + ": " + std::strerror(code));
    }
    bool open(Listener& listener) {
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        if (listener.path.empty() || listener.path.size() >= sizeof(address.sun_path) ||
            listener.path.find('\0') != std::string::npos)
            return fail(TransportError::invalid_argument, "invalid Unix socket path");
        std::memcpy(address.sun_path, listener.path.c_str(), listener.path.size() + 1);
        listener.fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (listener.fd < 0) return system_failure("socket");
        if (::bind(listener.fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
            const int code = errno;
            return fail(TransportError::io_error,
                        "bind " + listener.path + ": " + std::strerror(code) +
                        (code == EADDRINUSE
                             ? ". Another host may be running, or a previous run left a stale socket. "
                               "Check with ss -xapn; remove the socket only if no process owns it."
                             : ""));
        }
        if (::lstat(listener.path.c_str(), &listener.identity) < 0)
            return system_failure("lstat");
        listener.owned = true;
        if (::listen(listener.fd, 8) < 0) return system_failure("listen");
        return true;
    }
    int accept(Listener& listener) {
        int fd;
        do {
            if (!ready(listener.fd, POLLIN)) return -1;
            fd = ::accept4(listener.fd, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        } while (fd < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK));
        if (fd < 0) system_failure("accept");
        return fd;
    }
    bool read_all(int fd, std::span<std::byte> data) {
        while (!data.empty()) {
            if (!ready(fd, POLLIN)) return false;
            const auto count = ::recv(fd, data.data(), data.size(), 0);
            if (count < 0) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                return system_failure("receive");
            }
            if (count == 0) return fail(TransportError::disconnected, "incomplete gem5 packet");
            data = data.subspan(static_cast<std::size_t>(count));
        }
        return true;
    }
    bool send_packet(std::span<const std::byte> data) {
        Connection connection{accept(outgoing_)};
        if (connection.fd < 0) return false;
        // chimaeraRecv reads exactly the length requested by the guest.
        while (!data.empty()) {
            if (!ready(connection.fd, POLLOUT)) return false;
            const auto count = ::send(connection.fd, data.data(), data.size(), MSG_NOSIGNAL);
            if (count < 0) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                return system_failure("send");
            }
            if (count == 0) return fail(TransportError::disconnected, "peer disconnected during send");
            data = data.subspan(static_cast<std::size_t>(count));
        }
        return true;
    }
    bool receive_packet(std::span<std::byte> data) {
        Connection connection{accept(incoming_)};
        if (connection.fd < 0) return false;
        // chimaeraSend prefixes each op's bytes with a native uint64_t length.
        // This envelope is produced by gem5 on the same host, not by the guest.
        std::uint64_t size = 0;
        if (!read_all(connection.fd, std::as_writable_bytes(std::span(&size, 1))))
            return false;
        if (size != data.size())
            return fail(TransportError::invalid_argument, "unexpected gem5 packet length");
        return read_all(connection.fd, data);
    }
};

HostTransport::HostTransport(std::string g2h, std::string h2g)
    : implementation_(std::make_unique<Implementation>(std::move(g2h), std::move(h2g))) {}
HostTransport::~HostTransport() = default;
SendResult HostTransport::send(std::span<const std::byte> data) {
    return implementation_->send(data);
}
ReceiveResult HostTransport::receive() { return implementation_->receive(); }
void HostTransport::cancel() noexcept { implementation_->cancel(); }
} // namespace chimaera
