#include <mock_sim/mock_controller.hpp>
#include "controller_console.hpp"
#include <iostream>
#include <sstream>

using namespace mock_sim;
using namespace controller_example;

int main(int argc, char* argv[]) {
    if (argc > 2) {
        std::cerr << "Usage: " << argv[0] << " [ENDPOINT]\n";
        return 2;
    }
    try {
        const std::string endpoint = argc == 2 ? argv[1] : default_endpoint;
        std::size_t step = 0; // The startup interval is step zero on both sides.
        Queue outgoing(step);
        Display incoming("guest", step);
        MockTimingController timing;
        MockHostController controller(endpoint, timing, outgoing, incoming);
        std::cout << "Waiting for controller guest at " << endpoint << "...\n" << std::flush;
        require(controller.step(std::chrono::milliseconds(10), std::chrono::milliseconds(1)));
        std::cout << "Controller host connected. Startup step 0 complete.\n"
                     "send TEXT  queue a message (send alone queues an empty message)\n"
                     "step [interval_ms poll_ms]  advance time (default: 100 10)\n"
                     "quit       stop the guest\n"
                     "Guest is stopped at this prompt. Queued host messages reach it during the next step.\n";
        std::string line;
        while (std::cout << "host> " << std::flush, std::getline(std::cin, line)) {
            if (line == "quit" || line == "q") break;
            if (auto message = parse_send(line)) {
                std::cout << "[step " << step << "] Queued " << message->size()
                          << " bytes for step " << step + 1 << ".\n";
                outgoing.messages.push_back(std::move(*message));
            } else if (line == "step" || line.starts_with("step ")) {
                long long interval = 100, poll = 10;
                std::istringstream arguments(line.substr(4));
                arguments >> std::ws;
                if (!arguments.eof()) {
                    if (!(arguments >> interval >> poll)) {
                        std::cout << "Usage: step [interval_ms poll_ms]\n";
                        continue;
                    }
                    arguments >> std::ws;
                    if (!arguments.eof()) {
                        std::cout << "Unexpected extra argument.\n";
                        continue;
                    }
                }
                if (interval <= 0 || interval > 3600000 || poll <= 0 || poll > interval ||
                    1 + (interval - 1) / poll > 100000) {
                    std::cout << "Require 0 < poll <= interval <= 3600000 ms, at most 100000 polls.\n";
                    continue;
                }
                ++step;
                require(controller.step(std::chrono::milliseconds(interval), std::chrono::milliseconds(poll)));
                std::cout << "Interval " << step << " complete (" << interval << " ms).\n";
            } else {
                std::cout << "Commands: send TEXT, step [interval_ms poll_ms], quit.\n";
            }
        }
        require(controller.stop());
        std::cout << "Guest stopped.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Controller host: " << error.what() << '\n';
        return 1;
    }
}
