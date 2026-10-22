#pragma once

#include <chimaera/guest_transport.hpp>
#include <chimaera/message.hpp>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace chimaera {
enum class M5Lane : std::uint32_t { control = 0, bulk = 1 };
inline constexpr std::uint32_t m5_bulk_port_bit = 0x40000000U;
inline constexpr std::uint32_t m5_application_port_bit = 0x20000000U;

// One independent device fd, RX pool and notification generation per lane.
// Only use inside a gem5 guest. Address ops require libm5's mapping to exist.
// All methods are nonblocking, except setup/prefault in the constructor.
class M5Endpoint {
public:
    explicit M5Endpoint(M5Lane lane, std::string device = "/dev/chimaera-m5",
                        GuestM5Ops ops = GuestM5Ops::address);
    ~M5Endpoint();
    M5Endpoint(const M5Endpoint&) = delete;
    M5Endpoint& operator=(const M5Endpoint&) = delete;
    // Synchronously accepts the complete unchanged CDDS header and payload.
    // false means bounded backpressure: the caller retains both spans.
    bool send(std::span<const std::byte> header, std::span<const std::byte> body);
    void open_channel(std::uint32_t port);
    void close_channel(std::uint32_t port);
    // The Message owns a registered RX loan until its final view is destroyed.
    std::optional<Message> take();
    // Reap only packets ready now. The shared batch loan returns all its slots
    // when the final view is released; no timer or minimum batch size is used.
    std::vector<Message> take_batch();
    // Borrowed fd: poll for POLLIN, POLLERR or POLLHUP; never read this fd.
    int completion_fd() const;
private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
} // namespace chimaera
