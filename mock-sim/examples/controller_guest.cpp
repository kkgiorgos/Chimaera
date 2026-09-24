#include <mock_sim/mock_controller.hpp>
#include "controller_console.hpp"

#include <cerrno>
#include <csignal>
#include <iostream>
#include <poll.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
using namespace mock_sim;
using namespace controller_example;

// The controller calls take() only inside active intervals. No input thread or
// callback can run while the guest is stopped. poll() never waits for input.
class ConsoleProducer final : public DataProducer {
public:
    explicit ConsoleProducer(const std::size_t& step) : outgoing_("host", step) {}
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
                    if (!outgoing_.enqueue(*message))
                        throw std::runtime_error("guest outgoing queue full");
                    continue;
                }
                else std::cout << "Use send CHANNEL [TEXT], channel 1 or 2. Quit from the host.\n" << std::flush;
            } else {
                if (eof_) return outgoing_.take();
                pollfd input{STDIN_FILENO, POLLIN, 0};
                const auto ready = ::poll(&input, 1, 0);
                if (ready < 0) {
                    if (errno == EINTR) continue;
                    throw std::runtime_error("cannot poll terminal input");
                }
                if (ready == 0) return outgoing_.take();
                if (input.revents & (POLLERR | POLLNVAL))
                    throw std::runtime_error("terminal input is unavailable");
                // This worker is the only stdin reader. Read only ready input;
                // terminal canonical mode retains incomplete lines in the kernel.
                char bytes[4096];
                const auto count = ::read(STDIN_FILENO, bytes, sizeof(bytes));
                if (count < 0) {
                    if (errno == EINTR) continue;
                    if (errno == EAGAIN) return outgoing_.take();
                    throw std::runtime_error("cannot read terminal input");
                }
                if (count == 0) eof_ = true;
                else buffered_.append(bytes, static_cast<std::size_t>(count));
                if (buffered_.size() > 1024 * 1024)
                    throw std::runtime_error("console input exceeds 1 MiB");
            }
        }
        return outgoing_.take();
    }
private:
    Queue outgoing_;
    std::string buffered_;
    bool eof_{false};
};

int run_guest(const std::string& endpoint) {
    std::size_t step = 0;
    ConsoleProducer outgoing(step);
    Display incoming("host", step);
    MockGuestController controller(endpoint, outgoing, incoming);
    while (true) {
        auto result = controller.run_next();
        require(result);
        if (result.state == ControllerState::stopped) {
            std::cout << "Host ended the session.\n" << std::flush;
            return 0;
        }
        // Completion returns only on the following resume, before callbacks
        // for that next interval. Count intervals, not individual polling ticks.
        ++step;
    }
}
} // namespace

int main(int argc, char* argv[]) {
    if (argc > 2) {
        std::cerr << "Usage: " << argv[0] << " [ENDPOINT]\n";
        return 2;
    }
    const std::string endpoint = argc == 2 ? argv[1] : default_endpoint;
    std::cout << "Controller guest connecting to " << endpoint << "...\n"
                 "Type send CHANNEL [TEXT], channel 1 or 2; omit TEXT for empty data.\n"
                 "Input is read only during host intervals; while paused, typed lines wait in the terminal.\n"
                 "The host controls step and quit. EOF closes input but keeps receiving.\n" << std::flush;

    // Keep the shell's foreground job alive while the worker is SIGSTOPped.
    // This supervisor never reads stdin or participates in data transfer.
    const auto parent = ::getpid();
    const auto child = ::fork();
    if (child < 0) {
        std::cerr << "Cannot launch guest worker.\n";
        return 1;
    }
    if (child == 0) {
        // Include paused workers in cleanup when their terminal launcher dies.
        if (::prctl(PR_SET_PDEATHSIG, SIGKILL) < 0 || ::getppid() != parent) ::_exit(1);
        int status = 1;
        try { status = run_guest(endpoint); }
        catch (const std::exception& error) { std::cerr << "Controller guest: " << error.what() << '\n'; }
        ::_exit(status);
    }
    int status = 0;
    pid_t reaped;
    do { reaped = ::waitpid(child, &status, 0); } while (reaped < 0 && errno == EINTR);
    if (reaped < 0) {
        ::kill(child, SIGKILL);
        std::cerr << "Cannot wait for guest worker.\n";
        return 1;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    std::cerr << "Guest worker terminated by signal " << WTERMSIG(status) << ".\n";
    return 1;
}
