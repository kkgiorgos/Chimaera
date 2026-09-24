#include <chimaera/gem5_controller.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include "controller_console.hpp"
#include <cerrno>
#include <poll.h>
#include <unistd.h>

namespace {
using namespace chimaera;
using namespace chimaera::controller_example;

class ConsoleProducer final : public DataProducer {
public:
    std::optional<Message> take() override {
        // Bound work per poll even when input is continuously arriving.
        for (int attempt = 0; attempt < 32; ++attempt) {
            const auto newline = buffered_.find('\n');
            if (newline != std::string::npos || (eof_ && !buffered_.empty())) {
                const auto count = newline == std::string::npos ? buffered_.size() : newline;
                auto line = buffered_.substr(0, count);
                buffered_.erase(0, count + (newline != std::string::npos));
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (auto message = parse_send(line)) {
                    print_message("Sending to", "host", *message);
                    return message;
                }
                std::cout << "Use send TEXT (or send for empty data). Quit from the host.\n" << std::flush;
            } else {
                if (eof_) return std::nullopt;
                pollfd input{STDIN_FILENO, POLLIN, 0};
                const auto ready = ::poll(&input, 1, 0);
                if (ready < 0) {
                    if (errno == EINTR) continue;
                    throw std::runtime_error("cannot poll terminal input");
                }
                if (ready == 0) return std::nullopt;
                if (input.revents & (POLLERR | POLLNVAL))
                    throw std::runtime_error("terminal input is unavailable");
                // This worker is the only stdin reader. Read only ready input;
                // terminal canonical mode retains incomplete lines in the kernel.
                char bytes[4096];
                const auto count = ::read(STDIN_FILENO, bytes, sizeof(bytes));
                if (count < 0) {
                    if (errno == EINTR) continue;
                    if (errno == EAGAIN) return std::nullopt;
                    throw std::runtime_error("cannot read terminal input");
                }
                if (count == 0) eof_ = true;
                else buffered_.append(bytes, static_cast<std::size_t>(count));
                if (buffered_.size() > 1024 * 1024)
                    throw std::runtime_error("console input exceeds 1 MiB");
            }
        }
        return std::nullopt;
    }
private:
    std::string buffered_;
    bool eof_{false};
};

} // namespace

int main() {
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
    int status = 0;
    try {
        ConsoleProducer outgoing;
        Display incoming("host");
        Gem5GuestController controller(outgoing, incoming);
        std::cout << "Guest controller ready. Type send TEXT during running intervals.\n" << std::flush;
        // The boot-to-controller config pauses here before opening its timing socket.
        m5_work_begin_addr(0, 0);
        while (true) {
            const auto result = controller.run_next();
            require(result);
            if (result.state == ControllerState::stopped) break;
        }
    } catch (const std::exception& error) {
        std::cerr << "Guest controller: " << error.what() << '\n';
        status = 1;
    }
    unmap_m5_mem();
    return status;
}
