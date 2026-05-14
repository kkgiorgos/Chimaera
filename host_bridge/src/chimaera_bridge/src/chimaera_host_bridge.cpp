#include <cerrno>
#include <chrono>
#include <cinttypes>
#include <cstdint>
#include <cstddef>
#include <cstdlib>
#include <limits>
#include <memory>
#include <stdexcept>
#include <vector>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <string.h>

#include <rclcpp/rclcpp.hpp>

static constexpr const char* SOCKET_PATH = "/tmp/chimaera_g2h.sock";

union size_msg_t {
    char c[8];
    uint64_t l;
};

class ChimaeraHostBridge : public rclcpp::Node
{
public:
    ChimaeraHostBridge()
    : Node("chimaera_host_bridge")
    {
        setup_socket();
        accept_timer_ = create_wall_timer(
            std::chrono::milliseconds(10),
            [this]() { accept_clients(); });
    }

    ~ChimaeraHostBridge() override
    {
        if (server_fd_ != -1) {
            close(server_fd_);
        }
        unlink(SOCKET_PATH);
    }

private:
    int server_fd_ = -1;
    rclcpp::TimerBase::SharedPtr accept_timer_;

    [[noreturn]] void throw_errno(const char * operation)
    {
        const int error_number = errno;
        RCLCPP_ERROR(get_logger(), "%s: %s", operation, strerror(error_number));
        throw std::runtime_error(strerror(error_number));
    }

    bool read_exact(int fd, char * buff, size_t buff_size)
    {
        size_t total = 0;
        while (total != buff_size) {
            ssize_t rcnt = read(fd, buff + total, buff_size - total);
            if (rcnt == -1) {
                if (errno == EINTR) {
                    continue;
                }
                RCLCPP_ERROR(get_logger(), "read: %s", strerror(errno));
                return false;
            }
            if (rcnt == 0) {
                RCLCPP_ERROR(
                    get_logger(),
                    "Couldn't read the full message: read %zu out of %zu bytes.",
                    total,
                    buff_size);
                return false;
            }
            total += static_cast<size_t>(rcnt);
        }
        return true;
    }

    void setup_socket()
    {
        // Remove stale socket file if it exists.
        unlink(SOCKET_PATH);

        server_fd_ = socket(AF_UNIX, SOCK_STREAM, 0);
        if (server_fd_ == -1) {
            throw_errno("socket");
        }

        int flags = fcntl(server_fd_, F_GETFL, 0);
        if (flags == -1 || fcntl(server_fd_, F_SETFL, flags | O_NONBLOCK) == -1) {
            throw_errno("fcntl");
        }

        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

        if (bind(server_fd_, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) == -1) {
            throw_errno("bind");
        }

        if (listen(server_fd_, 16) == -1) {
            throw_errno("listen");
        }

        RCLCPP_INFO(get_logger(), "Server listening on %s", SOCKET_PATH);
    }

    void accept_clients()
    {
        while (rclcpp::ok()) {
            int client_fd = accept(server_fd_, nullptr, nullptr);
            if (client_fd == -1) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    return;
                }
                if (errno == EINTR) {
                    continue;
                }
                RCLCPP_ERROR(get_logger(), "accept: %s", strerror(errno));
                return;
            }

            handle_client(client_fd);
        }
    }

    void handle_client(int client_fd)
    {
        RCLCPP_INFO(get_logger(), "Client connected");

        size_msg_t msg_size;
        msg_size.l = 0;
        if (!read_exact(client_fd, msg_size.c, sizeof(msg_size.c))) {
            close(client_fd);
            return;
        }

        RCLCPP_INFO(get_logger(), "Expecting %" PRIu64 " bytes", msg_size.l);

        if (msg_size.l > std::numeric_limits<size_t>::max()) {
            RCLCPP_ERROR(get_logger(), "Message size %" PRIu64 " is too large", msg_size.l);
            close(client_fd);
            return;
        }

        std::vector<char> buf(static_cast<size_t>(msg_size.l));
        const size_t total = buf.size();

        if (!read_exact(client_fd, buf.data(), total)) {
            close(client_fd);
            return;
        }

        RCLCPP_INFO(get_logger(), "Client disconnected");

        RCLCPP_INFO(get_logger(), "Received %zu bytes", total);

        if (!buf.empty()) {
            RCLCPP_INFO(get_logger(), "First byte: 0x%x", static_cast<unsigned char>(buf.front()));
            RCLCPP_INFO(get_logger(), "Last byte: 0x%x", static_cast<unsigned char>(buf.back()));
        }

        close(client_fd);
    }
};

int main(int argc, char ** argv)
{
    rclcpp::init(argc, argv);

    try {
        rclcpp::spin(std::make_shared<ChimaeraHostBridge>());
    } catch (const std::exception & ex) {
        RCLCPP_ERROR(rclcpp::get_logger("chimaera_host_bridge"), "Fatal error: %s", ex.what());
        rclcpp::shutdown();
        return EXIT_FAILURE;
    }

    rclcpp::shutdown();
    return 0;
}
