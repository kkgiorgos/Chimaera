#pragma once

#include <chrono>
#include <csignal>
#include <fstream>
#include <filesystem>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

namespace mock_sim::detail {

// Outside the guest execution domain: inspect kernel state, never ask a stopped
// guest to acknowledge anything. pidfd signals cannot hit a recycled process ID.
class GuestProcess {
public:
    ~GuestProcess() { if (fd_ >= 0) ::close(fd_); }
    GuestProcess() = default;
    GuestProcess(const GuestProcess&) = delete;
    GuestProcess& operator=(const GuestProcess&) = delete;

    bool attached() const { return fd_ >= 0; }

    void attach(int pid) {
        if (pid <= 0 || pid == ::getpid())
            throw std::runtime_error("mock guest must run in a separate process");
        fd_ = static_cast<int>(::syscall(SYS_pidfd_open, pid, 0));
        if (fd_ < 0) throw std::runtime_error("pidfd_open failed (Linux 5.3+ required)");
        pid_ = pid;
    }

    void wait_paused() {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (true) {
            pollfd descriptor{fd_, POLLIN, 0};
            if (::poll(&descriptor, 1, 0) != 0)
                throw std::runtime_error("guest exited before pausing");
            bool all_paused = true;
            bool found = false;
            for (const auto& task : std::filesystem::directory_iterator(
                     "/proc/" + std::to_string(pid_) + "/task")) {
                std::ifstream status(task.path() / "stat");
                std::string line;
                std::getline(status, line);
                const auto name_end = line.rfind(')');
                found = true;
                if (name_end == std::string::npos || name_end + 2 >= line.size() ||
                    line[name_end + 2] != 'T') all_paused = false;
            }
            if (found && all_paused) return;
            if (std::chrono::steady_clock::now() >= deadline)
                throw std::runtime_error("guest did not enter the stopped state");
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }

    void resume() {
        if (::syscall(SYS_pidfd_send_signal, fd_, SIGCONT, nullptr, 0) < 0)
            throw std::runtime_error("cannot resume guest process");
    }

    // No unplanned execution outside an interval, even on host failure. Killing
    // also avoids a resume/stop race leaving an abandoned guest stopped forever.
    void terminate() noexcept {
        if (fd_ >= 0) ::syscall(SYS_pidfd_send_signal, fd_, SIGKILL, nullptr, 0);
    }

private:
    int fd_{-1};
    int pid_{-1};
};

} // namespace mock_sim::detail
