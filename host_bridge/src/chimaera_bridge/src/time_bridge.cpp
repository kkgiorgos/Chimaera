#include <cerrno>
#include <chrono>
#include <cinttypes>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

#include <rclcpp/rclcpp.hpp>

static constexpr const char * DEFAULT_TIME_SOCKET_PATH = "/tmp/chimaera_time.sock";

class TimeBridge : public rclcpp::Node
{
public:
    TimeBridge()
    : Node("time_bridge")
    {
        socket_path_ = declare_parameter<std::string>(
            "socket_path",
            DEFAULT_TIME_SOCKET_PATH);
        advance_ticks_ = declare_parameter<int64_t>("advance_ticks", 1000);
        poll_period_ms_ = declare_parameter<int64_t>("poll_period_ms", 10);

        if (advance_ticks_ <= 0) {
            throw std::runtime_error("advance_ticks must be positive");
        }
        if (poll_period_ms_ <= 0) {
            throw std::runtime_error("poll_period_ms must be positive");
        }

        loop_timer_ = create_wall_timer(
            std::chrono::milliseconds(poll_period_ms_),
            [this]() { sync_step(); });

        RCLCPP_INFO(
            get_logger(),
            "Time bridge client using %s, advance_ticks=%" PRId64
            ", poll_period_ms=%" PRId64,
            socket_path_.c_str(),
            advance_ticks_,
            poll_period_ms_);
    }

private:
    std::string socket_path_;
    int64_t advance_ticks_ = 0;
    int64_t poll_period_ms_ = 0;
    bool waiting_for_completion_ = false;
    uint64_t iteration_ = 0;
    rclcpp::TimerBase::SharedPtr loop_timer_;

    bool write_exact(int fd, const char * buff, size_t buff_size)
    {
        size_t total = 0;
        while (total != buff_size) {
            ssize_t wcnt = write(fd, buff + total, buff_size - total);
            if (wcnt == -1) {
                if (errno == EINTR) {
                    continue;
                }
                RCLCPP_ERROR(get_logger(), "write: %s", strerror(errno));
                return false;
            }
            if (wcnt == 0) {
                RCLCPP_ERROR(
                    get_logger(),
                    "Couldn't write the full command: wrote %zu out of %zu bytes.",
                    total,
                    buff_size);
                return false;
            }
            total += static_cast<size_t>(wcnt);
        }
        return true;
    }

    int connect_socket()
    {
        if (socket_path_.size() >= sizeof(sockaddr_un::sun_path)) {
            RCLCPP_ERROR(
                get_logger(),
                "Socket path is too long: %s",
                socket_path_.c_str());
            return -1;
        }

        int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd == -1) {
            RCLCPP_ERROR(get_logger(), "socket: %s", strerror(errno));
            return -1;
        }

        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        strncpy(addr.sun_path, socket_path_.c_str(), sizeof(addr.sun_path) - 1);

        if (connect(fd, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) == -1) {
            RCLCPP_WARN_THROTTLE(
                get_logger(),
                *get_clock(),
                1000,
                "connect %s: %s",
                socket_path_.c_str(),
                strerror(errno));
            close(fd);
            return -1;
        }

        return fd;
    }

    std::pair<bool, std::string> request(const std::string & command)
    {
        int fd = connect_socket();
        if (fd == -1) {
            return {false, ""};
        }

        if (!write_exact(fd, command.data(), command.size())) {
            close(fd);
            return {false, ""};
        }

        std::string response;
        char buf[256];
        while (true) {
            ssize_t rcnt = read(fd, buf, sizeof(buf));
            if (rcnt == -1) {
                if (errno == EINTR) {
                    continue;
                }
                RCLCPP_ERROR(get_logger(), "read: %s", strerror(errno));
                close(fd);
                return {false, ""};
            }
            if (rcnt == 0) {
                break;
            }
            response.append(buf, static_cast<size_t>(rcnt));
            if (response.find('\n') != std::string::npos) {
                break;
            }
        }

        close(fd);
        return {true, response};
    }

    bool response_is_ok(const std::string & response)
    {
        return response.find("OK") != std::string::npos ||
               response.find("\"ok\"") != std::string::npos ||
               response.find("\"accepted\"") != std::string::npos;
    }

    bool response_is_complete(const std::string & response)
    {
        return response.find("COMPLETE") != std::string::npos ||
               response.find("\"complete\": true") != std::string::npos ||
               response.find("\"complete\":true") != std::string::npos ||
               response.find("\"status\": \"complete\"") != std::string::npos ||
               response.find("\"status\":\"complete\"") != std::string::npos;
    }

    void sync_step()
    {
        if (!waiting_for_completion_) {
            const std::string command =
                "ADVANCE " + std::to_string(advance_ticks_) + "\n";
            const auto [success, response] = request(command);
            if (!success) {
                return;
            }
            if (!response_is_ok(response) && !response_is_complete(response)) {
                RCLCPP_WARN(
                    get_logger(),
                    "Advance command returned unexpected response: %s",
                    response.c_str());
                return;
            }

            waiting_for_completion_ = true;
            ++iteration_;
            RCLCPP_DEBUG(
                get_logger(),
                "Issued advance command for iteration %" PRIu64,
                iteration_);
            return;
        }

        const auto [success, response] = request("STATUS\n");
        if (!success) {
            return;
        }
        if (!response_is_complete(response)) {
            RCLCPP_DEBUG(
                get_logger(),
                "Iteration %" PRIu64 " still pending: %s",
                iteration_,
                response.c_str());
            return;
        }

        waiting_for_completion_ = false;
        RCLCPP_DEBUG(
            get_logger(),
            "Iteration %" PRIu64 " complete",
            iteration_);
    }
};

int main(int argc, char ** argv)
{
    rclcpp::init(argc, argv);

    try {
        rclcpp::spin(std::make_shared<TimeBridge>());
    } catch (const std::exception & ex) {
        RCLCPP_ERROR(rclcpp::get_logger("time_bridge"), "Fatal error: %s", ex.what());
        rclcpp::shutdown();
        return EXIT_FAILURE;
    }

    rclcpp::shutdown();
    return 0;
}
