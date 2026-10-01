#pragma once

#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>
#include <sys/ioctl.h>
#include <unistd.h>

namespace chimaera::controller_example {

// Reserve the final terminal row. Cursor save/restore keeps status refreshes
// away from the line being typed; normal output scrolls above the bar.
class StatusBar {
public:
    StatusBar() {
        const char* term = std::getenv("TERM");
        enabled_ = ::isatty(STDOUT_FILENO) && term && std::string_view(term) != "dumb";
    }
    ~StatusBar() { clear(); }
    StatusBar(const StatusBar&) = delete;
    StatusBar& operator=(const StatusBar&) = delete;

    void update(std::string_view text, bool explicit_report = false) {
        winsize size{};
        if (!enabled_ || ::ioctl(STDOUT_FILENO, TIOCGWINSZ, &size) < 0 ||
            size.ws_row < 3 || size.ws_col < 2) {
            clear();
            if (explicit_report) std::cout << text << '\n' << std::flush;
            return;
        }
        if (rows_ != size.ws_row) {
            clear();
            rows_ = size.ws_row;
            // Start output inside the scrolling region even if the cursor was
            // previously on the last row. Do not erase the message history.
            std::cout << "\033[1;" << rows_ - 1 << "r\033[" << rows_ - 1 << ";1H\n";
        }
        std::cout << "\0337\033[" << rows_ << ";1H\033[2K\033[7m"
                  << text.substr(0, size.ws_col - 1) << "\033[0m\0338" << std::flush;
    }

    void clear() {
        if (!rows_) return;
        std::cout << "\0337\033[" << rows_ << ";1H\033[2K\033[r\0338" << std::flush;
        rows_ = 0;
    }
private:
    bool enabled_{};
    unsigned rows_{};
};

} // namespace chimaera::controller_example
