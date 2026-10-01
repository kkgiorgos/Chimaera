#include <chimaera/channel_service.hpp>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace chimaera {
namespace {
constexpr std::size_t max_payload = 4096;
struct Fd {
    int value;
    explicit Fd(int v) : value(v) {}
    ~Fd() { if (value >= 0) ::close(value); }
};
sockaddr_un address(const std::string& path) {
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (path.empty() || path.size() >= sizeof(addr.sun_path))
        throw std::invalid_argument("invalid channel socket path");
    std::memcpy(addr.sun_path, path.c_str(), path.size() + 1);
    return addr;
}
void check(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(std::string(message) + ": " + std::strerror(errno));
}
}
struct ChannelService::Impl {
    std::string path;
    std::vector<QueueChannel> channels;
    QueueSerializer outgoing;
    QueueDeserializer incoming;
    Fd listener{-1};
    std::atomic<bool> stopped{false};
    std::thread worker;
    Impl(std::string p, std::span<const QueueChannel> c)
        : path(std::move(p)), channels(c.begin(), c.end()), outgoing(c), incoming(c) {
        // Keep each bundle below the controller's 32 MiB batch bound.
        std::size_t capacity = 0;
        for (const auto& channel : channels) {
            if (channel.depth > 4096 || capacity > 4096 - channel.depth)
                throw std::invalid_argument("channel service total depth exceeds 4096");
            capacity += channel.depth;
        }
        auto addr = address(path);
        listener.value = ::socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        check(listener.value >= 0, "channel socket");
        check(::bind(listener.value, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0, "bind channel socket");
        try {
            check(::chmod(path.c_str(), 0600) == 0, "channel socket permissions");
            check(::listen(listener.value, 32) == 0, "listen channel socket");
            worker = std::thread([this] { run(); });
        } catch (...) { ::unlink(path.c_str()); throw; }
    }
    void close() {
        stopped = true;
        incoming.close();
        ::shutdown(listener.value, SHUT_RDWR);
        if (worker.joinable()) worker.join();
    }
    ~Impl() { close(); ::unlink(path.c_str()); }
    void run() noexcept {
        std::vector<pollfd> peers{{listener.value, POLLIN, 0}};
        while (!stopped) {
            if (::poll(peers.data(), peers.size(), 20) < 0) {
                if (errno == EINTR) continue;
                break;
            }
            for (std::size_t i = peers.size(); i-- > 1;) {
                if (!peers[i].revents) continue;
                std::array<std::byte, max_payload + 5> packet{};
                auto n = ::recv(peers[i].fd, packet.data(), packet.size(), MSG_TRUNC);
                Message reply{std::byte{2}}; // Invalid request / channel.
                if (n >= 5 && n <= static_cast<ssize_t>(packet.size())) {
                    ChannelId id = 0;
                    for (int j = 1; j <= 4; ++j) id = (id << 8) | std::to_integer<unsigned>(packet[j]);
                    try {
                        if (packet[0] == std::byte{1}) {
                            reply[0] = outgoing.try_enqueue(id, std::span(packet).subspan(5, n - 5))
                                ? std::byte{0} : std::byte{1};
                        } else if (packet[0] == std::byte{2} && n == 5) {
                            auto data = incoming.try_dequeue(id);
                            reply[0] = data ? std::byte{0} : std::byte{1};
                            if (data) reply.insert(reply.end(), data->begin(), data->end());
                        }
                    } catch (...) { reply.assign(1, std::byte{2}); }
                }
                ::send(peers[i].fd, reply.data(), reply.size(), MSG_NOSIGNAL);
                ::close(peers[i].fd);
                peers.erase(peers.begin() + i);
            }
            if (peers[0].revents & POLLIN) {
                int fd = ::accept4(listener.value, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
                if (fd >= 0) {
                    if (peers.size() < 65) peers.push_back({fd, POLLIN, 0});
                    else ::close(fd);
                }
            }
        }
        for (std::size_t i = 1; i < peers.size(); ++i) ::close(peers[i].fd);
    }
};
ChannelService::ChannelService(std::string path, std::span<const QueueChannel> c)
    : impl_(std::make_unique<Impl>(std::move(path), c)) {}
ChannelService::~ChannelService() = default;
void ChannelService::close() { impl_->close(); }
std::optional<Message> ChannelService::take() {
    for (auto channel : impl_->channels)
        if (impl_->outgoing.size(channel.id)) return impl_->outgoing.serialize();
    return std::nullopt;
}
void ChannelService::submit(Message data) {
    if (!impl_->incoming.deserialize(data)) throw std::runtime_error("channel service closed");
}
ChannelClient::ChannelClient(std::string path, ChannelId id) : socket_(std::move(path)), channel_(id) {}
Message ChannelClient::request(unsigned char operation, std::span<const std::byte> payload) {
    if (payload.size() > max_payload) throw std::invalid_argument("channel payload exceeds 4096 bytes");
    auto addr = address(socket_);
    Fd fd(::socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0));
    check(fd.value >= 0, "channel client socket");
    check(::connect(fd.value, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0, "connect channel service");
    Message packet{std::byte(operation)};
    for (int shift = 24; shift >= 0; shift -= 8) packet.push_back(std::byte((channel_ >> shift) & 255));
    packet.insert(packet.end(), payload.begin(), payload.end());
    check(::send(fd.value, packet.data(), packet.size(), MSG_NOSIGNAL) == static_cast<ssize_t>(packet.size()), "send channel request");
    std::array<std::byte, max_payload + 1> buffer{};
    ssize_t n;
    do { n = ::recv(fd.value, buffer.data(), buffer.size(), MSG_TRUNC); } while (n < 0 && errno == EINTR);
    if (n < 1 || n > static_cast<ssize_t>(buffer.size())) throw std::runtime_error("channel service disconnected or invalid reply");
    if (buffer[0] != std::byte{0} && buffer[0] != std::byte{1}) throw std::runtime_error("unknown channel or invalid request");
    return Message(buffer.begin(), buffer.begin() + n);
}
bool ChannelClient::send(std::span<const std::byte> data) { return request(1, data)[0] == std::byte{0}; }
std::optional<Message> ChannelClient::receive() {
    auto reply = request(2, {});
    if (reply[0] == std::byte{1}) return std::nullopt;
    reply.erase(reply.begin());
    return reply;
}
}
