#include <chimaera/gem5_controller.hpp>
#include <chimaera/wall_clock_pacer.hpp>
#include "controller_console.hpp"
#include "status_bar.hpp"

#include <algorithm>
#include <cerrno>
#include <charconv>
#include <cmath>
#include <csignal>
#include <iomanip>
#include <sstream>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <string_view>
#include <thread>
#include <unistd.h>

namespace {
using namespace chimaera;
using namespace chimaera::controller_example;
using Clock = std::chrono::steady_clock;
volatile std::sig_atomic_t interrupted = 0;
void interrupt(int) { interrupted = 1; }

struct Options {
    std::string endpoint = "/tmp/chimaera_time.sock";
    double ratio = 1.0; // Simulated seconds per wall-clock second.
    long long interval_us = 100000;
    long long poll_us = 10000;
    long long startup_timeout = 300;
    long long steps = 0; // Zero means run until quit or a signal.
    double report_seconds = 1.0;
};
void usage(const char* name) {
    std::cout << "Usage: " << name << " [TIMING_SOCKET] [options]\n"
                 "  --socket PATH          gem5 timing socket\n"
                 "  --ratio R              simulated seconds / wall second (default: 1)\n"
                 "  --interval-us N        sync interval in us (default: 100000)\n"
                 "  --poll-us N            guest poll interval in us (default: 10000)\n"
                 "  --report-seconds S     rate report period (default: 1)\n"
                 "  --startup-timeout N    seconds to wait for gem5 (default: 300)\n"
                 "  --steps N              stop after N intervals (default: unlimited)\n"
                 "  --help                 show this help\n";
}
long long integer(std::string_view text) {
    long long value{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size())
        throw std::invalid_argument("invalid integer: " + std::string(text));
    return value;
}
double positive_real(const std::string& text) {
    std::size_t end;
    const double value = std::stod(text, &end);
    if (end != text.size() || !std::isfinite(value) || value <= 0)
        throw std::invalid_argument("expected a finite positive number: " + text);
    return value;
}
Options parse(int argc, char** argv) {
    Options options;
    bool positional = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (!arg.starts_with("--")) {
            if (positional) throw std::invalid_argument("unexpected argument: " + arg);
            options.endpoint = arg;
            positional = true;
            continue;
        }
        if (i + 1 == argc) throw std::invalid_argument("missing value for " + arg);
        const std::string value = argv[++i];
        if (arg == "--socket") options.endpoint = value;
        else if (arg == "--ratio") options.ratio = positive_real(value);
        else if (arg == "--interval-us") options.interval_us = integer(value);
        else if (arg == "--poll-us") options.poll_us = integer(value);
        else if (arg == "--report-seconds") options.report_seconds = positive_real(value);
        else if (arg == "--startup-timeout") options.startup_timeout = integer(value);
        else if (arg == "--steps") options.steps = integer(value);
        else throw std::invalid_argument("unknown option: " + arg);
    }
    if (options.interval_us <= 0 || options.interval_us > 3600000000LL ||
        options.poll_us <= 0 || options.poll_us > options.interval_us ||
        1 + (options.interval_us - 1) / options.poll_us > 100000)
        throw std::invalid_argument("require 0 < poll <= interval <= 3600000000 us, at most 100000 polls");
    if (options.steps < 0 || options.startup_timeout <= 0 || options.startup_timeout > 86400 ||
        options.report_seconds > 86400)
        throw std::invalid_argument("invalid step limit, startup timeout, or report period");
    return options;
}

// Read complete commands without blocking automatic stepping on a partial line.
class ConsoleInput {
public:
    template<class Handler>
    void read(int wait_us, Handler handle) {
        if (eof_) {
            if (wait_us > 0) std::this_thread::sleep_for(std::chrono::microseconds(wait_us));
            return;
        }
        for (int attempt = 0; attempt < 16; ++attempt) {
            pollfd descriptor{STDIN_FILENO, POLLIN, 0};
            const auto timeout_us = attempt == 0 ? wait_us : 0;
            const timespec timeout{timeout_us / 1000000, (timeout_us % 1000000) * 1000L};
            const int ready = ::ppoll(&descriptor, 1, &timeout, nullptr);
            if (ready < 0 && errno == EINTR) return;
            if (ready < 0) throw std::runtime_error("cannot poll console input");
            if (ready == 0) return;
            if (descriptor.revents & (POLLERR | POLLNVAL)) { eof_ = true; return; }
            char bytes[4096];
            const auto count = ::read(STDIN_FILENO, bytes, sizeof(bytes));
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) return;
            if (count < 0) throw std::runtime_error("cannot read console input");
            if (count == 0) eof_ = true;
            else buffered_.append(bytes, static_cast<std::size_t>(count));
            if (buffered_.size() > 1024 * 1024) throw std::runtime_error("console line exceeds 1 MiB");
            while (true) {
                const auto newline = buffered_.find('\n');
                if (newline == std::string::npos && !(eof_ && !buffered_.empty())) break;
                auto line = buffered_.substr(0, newline);
                buffered_.erase(0, newline == std::string::npos ? buffered_.size() : newline + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                handle(line);
            }
            if (eof_) return;
        }
    }
private:
    bool eof_ = false;
    std::string buffered_;
};
} // namespace

int main(int argc, char* argv[]) {
    for (int i = 1; i < argc; ++i) {
        if (std::string_view(argv[i]) == "--help") { usage(argv[0]); return 0; }
    }
    std::signal(SIGINT, interrupt);
    std::signal(SIGTERM, interrupt);
    try {
        const auto options = parse(argc, argv);
        std::size_t step = 0;
        Queue outgoing;
        Display incoming("guest");
        Gem5TimingController timing(options.endpoint);
        Gem5HostController controller(timing, outgoing, incoming);
        std::cout << "Host data listeners ready. Waiting for gem5 at " << options.endpoint
                  << "...\n" << std::flush;
        try {
            timing.wait_until_ready(std::chrono::seconds(options.startup_timeout),
                                    [] { return interrupted != 0; });
        } catch (...) {
            if (interrupted) return 0;
            throw;
        }
        WallClockPacer pacer(options.ratio); // Startup and guest boot are excluded.
        ConsoleInput input;
        bool quit = false;
        auto last_report = Clock::now();
        StatusBar status_bar;
        auto report = [&](bool final = false, bool explicit_report = false) {
            const auto rate = pacer.report(timing.elapsed_ticks());
            std::ostringstream text;
            text << (final ? "Final rate: " : "Rate: ") << std::setprecision(6)
                      << "target=" << pacer.target_ratio() << "x ";
            if (!final) text << "achieved=" << rate.achieved_ratio << "x ";
            text << "average=" << rate.average_ratio << "x sim=" << rate.simulated_seconds
                      << "s wall=" << rate.wall_seconds << "s";
            if (final) {
                status_bar.clear();
                std::cout << text.str() << '\n' << std::flush;
            } else {
                status_bar.update(text.str(), explicit_report);
            }
            last_report = Clock::now();
        };
        auto command = [&](const std::string& line) {
            if (line == "quit" || line == "q") quit = true;
            else if (line == "status") report(false, true);
            else if (auto message = parse_send(line)) outgoing.messages.push_back(std::move(*message));
            else if (!line.empty()) std::cout << "Commands: send TEXT, status, quit. Timing runs automatically.\n";
        };
        std::cout << "Automatic pacing: target " << options.ratio << " simulated seconds / wall second.\n"
                     "Commands: send TEXT, status, quit. Ctrl+C stops after the current interval.\n" << std::flush;
        report();
        while (!quit && !interrupted && (options.steps == 0 || step < static_cast<std::size_t>(options.steps))) {
            input.read(0, command);
            if (quit || interrupted) break;
            ++step;
            require(controller.step(std::chrono::microseconds(options.interval_us),
                                    std::chrono::microseconds(options.poll_us)));
            while (!quit && !interrupted) {
                if (std::chrono::duration<double>(Clock::now() - last_report).count() >= options.report_seconds)
                    report();
                const auto delay = pacer.delay(timing.elapsed_ticks());
                if (delay <= std::chrono::nanoseconds::zero()) break; // Already behind: no sleep.
                const auto us = std::chrono::ceil<std::chrono::microseconds>(delay).count();
                const double until_report = options.report_seconds -
                    std::chrono::duration<double>(Clock::now() - last_report).count();
                const auto report_us = static_cast<std::int64_t>(std::ceil(std::max(0.0, until_report) * 1000000));
                input.read(static_cast<int>(std::max<std::int64_t>(1, std::min({us, report_us, std::int64_t{100000}}))), command);
            }
        }
        report(true);
        require(controller.stop());
        std::cout << "gem5 shut down.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Controller host: " << error.what() << '\n';
        return 1;
    }
}
