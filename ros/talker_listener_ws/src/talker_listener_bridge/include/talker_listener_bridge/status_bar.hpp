#pragma once

#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <string>
#include <string_view>

namespace talker_listener_bridge
{
// Like the transport demo, reserve the last row and preserve the log history.
// ros2 launch captures stdout in a pipe: when its own output is a terminal it
// explicitly permits direct access to /dev/tty, avoiding launch log prefixes.
class StatusBar
{
public:
  explicit StatusBar(bool enabled)
  {
    const char * term = std::getenv("TERM");
    const char * launch_tty = std::getenv("CHIMAERA_STATUS_TTY");
    if (
      enabled && term && std::string_view(term) != "dumb" &&
      (::isatty(STDOUT_FILENO) || (launch_tty && std::string_view(launch_tty) == "1"))) {
      fd_ = ::open("/dev/tty", O_WRONLY | O_CLOEXEC | O_NOCTTY);
    }
  }
  ~StatusBar()
  {
    clear();
    if (fd_ >= 0) {
      ::close(fd_);
    }
  }
  StatusBar(const StatusBar &) = delete;
  StatusBar & operator=(const StatusBar &) = delete;

  void update(std::string_view text)
  {
    winsize size{};
    if (fd_ < 0) {
      return;
    }
    if (::ioctl(fd_, TIOCGWINSZ, &size) < 0 || size.ws_row < 3 || size.ws_col < 2) {
      clear();
      return;
    }
    if (rows_ != size.ws_row) {
      clear();
      rows_ = size.ws_row;
      write("\033[1;" + std::to_string(rows_ - 1) + "r\033[" + std::to_string(rows_ - 1) + ";1H\n");
    }
    // ASCII text gives predictable clipping and cannot inject terminal controls.
    std::string clipped(text.substr(0, size.ws_col - 1));
    for (char & c : clipped) {
      if (static_cast<unsigned char>(c) < 32 || static_cast<unsigned char>(c) > 126) {
        c = '?';
      }
    }
    write("\0337\033[" + std::to_string(rows_) + ";1H\033[2K\033[7m" + clipped + "\033[0m\0338");
  }

  void clear()
  {
    if (!rows_) {
      return;
    }
    write("\0337\033[" + std::to_string(rows_) + ";1H\033[2K\033[r\0338");
    rows_ = 0;
  }

private:
  void write(std::string_view bytes)
  {
    while (!bytes.empty()) {
      const auto n = ::write(fd_, bytes.data(), bytes.size());
      if (n < 0 && errno == EINTR) {
        continue;
      }
      if (n <= 0) {
        break;
      }
      bytes.remove_prefix(static_cast<std::size_t>(n));
    }
  }
  int fd_{-1};
  unsigned rows_{};
};
}  // namespace talker_listener_bridge
