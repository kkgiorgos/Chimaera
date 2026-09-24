#include <chimaera/gem5_controller.hpp>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <limits>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace chimaera {
struct Gem5TimingController::Impl {
    std::string endpoint;
    std::chrono::seconds timeout;
    std::chrono::steady_clock::time_point deadline;
    int fd{-1};
    static constexpr std::uint64_t max_step_ticks = 3'600'000'000'000'000;
    std::uint64_t nominal_ticks{};
    std::uint64_t target_tick{};
    std::uint64_t last_tick{};
    std::uint64_t first_tick{};
    bool anchored{false};
    bool stopped{false};
    bool failed{false};
    bool finished{false};
    Impl(std::string path, std::chrono::seconds limit) : endpoint(std::move(path)), timeout(limit) {
        if (timeout.count() <= 0 || timeout > std::chrono::hours(24))
            throw std::invalid_argument("timing timeout must be positive and at most 24 hours");
    }
    ~Impl() { close(); }
    void close() { if (fd >= 0) ::close(fd); fd = -1; }
    [[noreturn]] void error(const std::string& message) {
        failed = true;
        close();
        throw std::runtime_error("gem5 timing: " + message);
    }
    void ready(short events) {
        while (true) {
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now()).count();
            if (left <= 0) error("socket timeout; simulator state is unknown");
            pollfd descriptor{fd, events, 0};
            const int result = ::poll(&descriptor, 1, static_cast<int>(left));
            if (result < 0 && errno == EINTR) continue;
            if (result < 0) error(std::strerror(errno));
            if (result == 0) error("socket timeout; simulator state is unknown");
            return;
        }
    }
    bool request(const std::string& command, bool allow_unavailable = false) {
        if (fd >= 0) throw std::logic_error("a gem5 timing command is already pending");
        if (stopped || failed) throw std::logic_error("gem5 timing session is stopped or failed");
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        if (endpoint.empty() || endpoint.size() >= sizeof(address.sun_path) ||
            endpoint.find('\0') != std::string::npos)
            throw std::invalid_argument("invalid timing socket path");
        std::memcpy(address.sun_path, endpoint.c_str(), endpoint.size() + 1);
        deadline = std::chrono::steady_clock::now() + timeout;
        fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd < 0) error(std::strerror(errno));
        if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
            if (allow_unavailable && (errno == ENOENT || errno == ECONNREFUSED)) {
                close();
                return false;
            }
            if (errno != EINPROGRESS) error("connect " + endpoint + ": " + std::strerror(errno));
            ready(POLLOUT);
            int code = 0;
            socklen_t length = sizeof(code);
            if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &code, &length) < 0)
                error(std::strerror(errno));
            if (code) error(std::strerror(code));
        }
        std::size_t offset = 0;
        while (offset < command.size()) {
            ready(POLLOUT);
            const auto count = ::send(fd, command.data() + offset, command.size() - offset, MSG_NOSIGNAL);
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (count <= 0) error("cannot send timing command");
            offset += static_cast<std::size_t>(count);
        }
        return true;
    }
    std::string response() {
        if (fd < 0) throw std::logic_error("no gem5 timing command is pending");
        std::string line;
        while (line.size() < 4096) {
            ready(POLLIN);
            char byte;
            const auto count = ::recv(fd, &byte, 1, 0);
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (count <= 0) error("timing server disconnected before replying");
            if (byte == '\n') { close(); return line; }
            line.push_back(byte);
        }
        error("oversized timing response");
    }
};

Gem5TimingController::Gem5TimingController(std::string endpoint, std::chrono::seconds timeout)
    : impl_(std::make_unique<Impl>(std::move(endpoint), timeout)) {}
Gem5TimingController::~Gem5TimingController() = default;
void Gem5TimingController::start(Duration interval) {
    auto& s = *impl_;
    if (s.finished) throw std::logic_error("gem5 has finished; only shutdown is available");
    if (interval.count() <= 0 || interval > std::chrono::hours(1))
        throw std::invalid_argument("interval must be positive and at most one hour");
    const auto nominal = static_cast<std::uint64_t>(interval.count()) * 1000;
    if (s.anchored && nominal > std::numeric_limits<std::uint64_t>::max() - s.target_tick)
        throw std::overflow_error("gem5 cumulative timing target exceeds tick range");
    const auto target = s.anchored ? s.target_tick + nominal : 0;
    // Preserve the ideal timeline, not the previous actual stopping point.
    // If gem5 is already ahead of the next boundary, keep it paused this step.
    auto ticks = s.anchored ? (target > s.last_tick ? target - s.last_tick : 0) : nominal;
    if (ticks > Impl::max_step_ticks) ticks = Impl::max_step_ticks;
    s.request("STEP_TICKS " + std::to_string(ticks) + "\n");
    s.nominal_ticks = nominal;
    if (s.anchored) s.target_tick = target;
}
void Gem5TimingController::wait() {
    auto& s = *impl_;
    const auto line = s.response();
    std::istringstream input(line);
    std::string status, extra;
    std::uint64_t start = 0, end = 0;
    if (input >> status; status == "DONE") {
        if (!(input >> end) || (input >> extra)) s.error("invalid completion response: " + line);
        s.finished = true;
        throw std::runtime_error("gem5 finished before completing the interval at tick " + std::to_string(end));
    }
    input.clear();
    input.str(line);
    if (!(input >> status >> start >> end) || status != "OK" || (input >> extra) || end < start)
        s.error("invalid interval response: " + line);
    if (s.anchored && start != s.last_tick)
        s.error("simulation tick changed outside this timing session: " + line);
    if (!s.anchored) {
        if (s.nominal_ticks > std::numeric_limits<std::uint64_t>::max() - start)
            s.error("initial timing target exceeds tick range");
        s.target_tick = start + s.nominal_ticks;
        s.first_tick = start;
        s.anchored = true;
    }
    // Both undershoot and overshoot are carried into the next start request,
    // including sub-nanosecond errors (all accounting is in integer ticks).
    s.last_tick = end;
}
std::uint64_t Gem5TimingController::elapsed_ticks() const noexcept {
    return impl_->anchored ? impl_->last_tick - impl_->first_tick : 0;
}
void Gem5TimingController::wait_until_ready(std::chrono::seconds timeout,
                                          const std::function<bool()>& cancelled) {
    auto& s = *impl_;
    if (timeout.count() <= 0 || timeout > std::chrono::hours(24))
        throw std::invalid_argument("startup timeout must be positive and at most 24 hours");
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (!s.request("STATUS\n", true)) {
        if (cancelled && cancelled()) throw std::runtime_error("startup cancelled");
        if (std::chrono::steady_clock::now() >= deadline)
            throw std::runtime_error("timed out waiting for gem5 timing socket " + s.endpoint);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    s.deadline = std::min(s.deadline, deadline);
    const auto line = s.response();
    std::istringstream input(line);
    std::string state, extra;
    std::uint64_t tick;
    if (!(input >> state >> tick) || state != "PAUSED" || (input >> extra))
        s.error("simulator is not ready: " + line);
}
void Gem5TimingController::shutdown() {
    auto& s = *impl_;
    if (s.stopped) return;
    if (s.fd >= 0) wait();
    s.request("QUIT\n");
    const auto line = s.response();
    if (line != "BYE") s.error("unexpected shutdown response: " + line);
    s.stopped = true;
}
} // namespace chimaera
