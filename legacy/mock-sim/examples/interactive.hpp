#pragma once

#include <mock_sim/transport.hpp>

#include <iostream>
#include <span>
#include <string>
#include <string_view>

namespace example {

inline int run_interactive(mock_sim::Transport& transport, std::string_view side) {
    std::cout << side << ": choose send on one side and receive on the other.\n";
    std::string mode;
    while (true) {
        std::cout << side << " mode [s/send, r/receive, q/quit]: " << std::flush;
        if (!std::getline(std::cin, mode) || mode == "q" || mode == "quit") {
            return 0;
        }
        if (mode == "s" || mode == "send") {
            std::cout << "Message: " << std::flush;
            std::string message;
            if (!std::getline(std::cin, message)) {
                return 0;
            }
            const auto sent = transport.send(std::as_bytes(std::span(message)));
            if (!sent.ok()) {
                std::cerr << side << " send failed: " << sent.message << '\n';
                return 1;
            }
            std::cout << "Sent " << message.size() << " bytes.\n";
        } else if (mode == "r" || mode == "receive") {
            std::cout << "Waiting for a message...\n" << std::flush;
            const auto received = transport.receive();
            if (!received.ok()) {
                std::cerr << side << " receive failed: " << received.message << '\n';
                return 1;
            }
            std::cout << "Received " << received.data.size() << " bytes: ";
            if (!received.data.empty()) {
                std::cout.write(reinterpret_cast<const char*>(received.data.data()),
                                static_cast<std::streamsize>(received.data.size()));
            }
            std::cout << '\n';
        } else {
            std::cout << "Choose s/send, r/receive, or q/quit.\n";
        }
    }
}

} // namespace example
